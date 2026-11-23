import atexit
import sqlite3
import threading
from flask import g

from pb.config import DATABASE_URL, DB_PATH
from pb.web import app


if DATABASE_URL:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

# ---------------------------------------------------------------- database

def connect(url=None):
    if DATABASE_URL:
        # prepare_threshold=None: no server-side prepared statements through Neon's PgBouncer pooler
        return psycopg.connect(
            url or DATABASE_URL, row_factory=dict_row, prepare_threshold=None, connect_timeout=DB_CONNECT_TIMEOUT
        )
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


# A USSD screen must answer within ~10 s or the network gives up ("USSD code running..." then an
# error), so a slow database fails fast with "please dial again" instead of hanging the phone.
DB_CONNECT_TIMEOUT = 5
DB_WAIT_TIMEOUT = 5  # waiting for a free pooled connection

_pool = None
_pool_lock = threading.Lock()


def pool():
    """Reuse Postgres connections across requests: opening one to Neon costs several round trips.

    Created on first use so each gunicorn worker gets its own (pools don't survive fork).
    """
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ConnectionPool(
                DATABASE_URL,
                min_size=1,
                max_size=4,  # = gunicorn threads per worker (gunicorn.conf.py)
                timeout=DB_WAIT_TIMEOUT,
                max_idle=240,  # Neon may close idle connections; drop ours first
                check=ConnectionPool.check_connection,  # replace connections Neon closed while idle
                kwargs={"row_factory": dict_row, "prepare_threshold": None, "connect_timeout": DB_CONNECT_TIMEOUT},
                open=True,
            )
            atexit.register(_pool.close)
    return _pool


def get_db():
    if "db" not in g:
        g.db = pool().getconn() if DATABASE_URL else connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is None:
        return
    if DATABASE_URL:
        pool().putconn(db)  # rolls back anything uncommitted
    else:
        db.close()


def sql(query):
    """Queries are written with SQLite's ? placeholders; Postgres wants %s."""
    return query.replace("?", "%s") if DATABASE_URL else query


def fetch_all(query, params=()):
    return get_db().execute(sql(query), params).fetchall()


def fetch_one(query, params=()):
    return get_db().execute(sql(query), params).fetchone()


def execute(query, params=()):
    db = get_db()
    db.execute(sql(query), params)
    db.commit()


def add_column(db, table, column, definition):
    if DATABASE_URL:
        db.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")
    elif column not in [row[1] for row in db.execute(f"PRAGMA table_info({table})")]:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
