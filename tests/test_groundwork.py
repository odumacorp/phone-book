"""Phase 0 groundwork: numbered migrations and the background-job tick."""
import time

from conftest import app_module
from pb import jobs
from pb.db import connect, fetch_all, fetch_one
from pb.migrations import available, run_migrations


def test_migrations_recorded_once():
    with app_module.app.app_context():
        versions = [r["version"] for r in fetch_all("SELECT version FROM schema_migrations ORDER BY version")]
    assert versions == [v for v, _ in available()]
    assert versions[:2] == ["0001", "0002"]
    db = connect()
    try:
        run_migrations(db)  # running again applies nothing new
        db.commit()
    finally:
        db.close()
    with app_module.app.app_context():
        assert len(fetch_all("SELECT version FROM schema_migrations")) == len(versions)


def test_tick_needs_secret(client):
    assert client.post("/internal/tick").status_code == 403
    assert client.post("/internal/tick", headers={"X-Tick-Secret": "wrong"}).status_code == 403
    assert client.get("/internal/tick", headers={"X-Tick-Secret": "tick-secret"}).status_code == 405


def test_tick_runs_due_jobs_once(client, monkeypatch):
    calls = []
    monkeypatch.setitem(jobs.JOBS, "test_job", (3600, lambda: calls.append(1)))
    resp = client.post("/internal/tick", headers={"X-Tick-Secret": "tick-secret"})
    assert resp.status_code == 200
    assert resp.get_json()["ran"]["test_job"] == "ok"
    assert resp.get_json()["ran"]["close_idle_ussd_logins"] == "ok"
    resp = client.post("/internal/tick", headers={"X-Tick-Secret": "tick-secret"})
    assert "test_job" not in resp.get_json()["ran"]  # not due again for an hour
    assert calls == [1]


def test_failing_job_is_recorded(client, monkeypatch):
    def boom():
        raise ValueError("nope")
    monkeypatch.setitem(jobs.JOBS, "bad_job", (60, boom))
    resp = client.post("/internal/tick", headers={"X-Tick-Secret": "tick-secret"})
    assert resp.get_json()["ran"]["bad_job"] == "error"
    with app_module.app.app_context():
        row = fetch_one("SELECT last_status, last_error FROM job_runs WHERE name = 'bad_job'")
    assert row["last_status"] == "error" and "ValueError" in row["last_error"]


def test_idle_ussd_logins_closed_by_job(register, dial, client):
    register()
    s = dial("+254712345678")
    s.send("0712345678")
    s.send("1234")
    with app_module.app.app_context():
        from pb.db import execute
        execute("UPDATE ussd_sessions SET last_active = ?", (int(time.time()) - 120,))
    client.post("/internal/tick", headers={"X-Tick-Secret": "tick-secret"})
    with app_module.app.app_context():
        row = fetch_one("SELECT ended_at, end_reason FROM ussd_sessions")
    assert row["ended_at"] and row["end_reason"] == "timed out"


def test_running_job_is_not_claimed_twice():
    now = int(time.time())
    with app_module.app.app_context():
        assert jobs.claim("x", 60, now) is True
        assert jobs.claim("x", 60, now + 120) is False  # still running (never finished)
        assert jobs.claim("x", 60, now + jobs.STALE_SECONDS + 1) is True  # presumed dead: claimed again
