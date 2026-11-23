"""Background jobs, run by an external scheduler calling POST /internal/tick (e.g. every minute).

The app has no job runner of its own (gunicorn workers come and go, and Render can restart them at any
time), so a cron service calls the tick endpoint with the secret in the X-Tick-Secret header:

    curl -X POST -H "X-Tick-Secret: $TICK_SECRET" https://<host>/internal/tick

Each tick runs the jobs whose interval has passed. A job is claimed with one atomic UPDATE first,
so overlapping ticks (or two servers) never run the same job at once. The tick endpoint doesn't
exist (404) until TICK_SECRET is set.

Register a job with:

    @job("retention", every=3600)
    def apply_retention(): ...
"""
import hmac
import time
import traceback

from flask import abort, jsonify, request

from pb.config import TICK_SECRET, log
from pb.db import execute, get_db, sql
from pb.ussd.session import close_idle_ussd_logins
from pb.web import app

JOBS = {}  # name -> (every seconds, function)


def job(name, every):
    def register(fn):
        JOBS[name] = (every, fn)
        return fn
    return register


STALE_SECONDS = 15 * 60  # a run that started this long ago without finishing is assumed dead


def claim(name, every, now):
    """Atomically mark a job as started if it's due and not already running. True if this process got it.

    One conditional UPDATE, so it is safe across workers and servers and through Neon's pooler
    (where session-level advisory locks aren't reliable).
    """
    db = get_db()
    db.execute(sql("INSERT INTO job_runs (name, last_started) VALUES (?, 0) ON CONFLICT (name) DO NOTHING"), (name,))
    cur = db.execute(
        sql(
            "UPDATE job_runs SET last_started = ?, last_finished = NULL WHERE name = ? AND last_started <= ? "
            "AND (last_finished IS NOT NULL OR last_started = 0 OR last_started <= ?)"
        ),
        (now, name, now - every, now - STALE_SECONDS),
    )
    db.commit()
    return cur.rowcount == 1


def run_job(name, now=None):
    """Run one job if it is due and nobody else is running it. Returns "ok", "error" or None (skipped)."""
    every, fn = JOBS[name]
    if not claim(name, every, now or int(time.time())):
        return None
    try:
        fn()
        status, error = "ok", None
    except Exception:
        get_db().rollback()
        log.exception("job %s failed", name)
        status, error = "error", traceback.format_exc()[-2000:]
    execute(
        "UPDATE job_runs SET last_finished = ?, last_status = ?, last_error = ? WHERE name = ?",
        (int(time.time()), status, error, name),
    )
    return status


def tick(now=None):
    now = now or int(time.time())
    ran = {name: run_job(name, now) for name in sorted(JOBS)}
    return {name: status for name, status in ran.items() if status}


@app.route("/internal/tick", methods=["POST"])
def internal_tick():
    if not TICK_SECRET:
        abort(404)
    if not hmac.compare_digest(request.headers.get("X-Tick-Secret", "").encode(), TICK_SECRET.encode()):
        abort(403)
    return jsonify({"ran": tick()})


# ---------------------------------------------------------------- jobs

@job("close_idle_ussd_logins", every=60)
def _close_idle_ussd_logins():
    """USSD logins are also closed when someone dials; this keeps the session history right when nobody does."""
    close_idle_ussd_logins()
