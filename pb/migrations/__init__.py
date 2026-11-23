"""Numbered schema migrations, run in order at startup after the original tables are created.

Each migration is a module named NNNN_short_name.py in this folder with a function:

    def up(db, m):          # db: an open connection; m: Helpers (dialect-aware SQL bits)
        db.execute(f"CREATE TABLE ... (id {m.id_col}, ...)")

Rules:
- Migrations only add (tables, columns, indexes, seed rows). Never edit one that has shipped: add a new one.
- They run inside init_db's transaction, under its Postgres advisory lock, so two workers starting
  together can't both run one. Applied migrations are recorded in schema_migrations.
- Write SQL that works on both SQLite (local, tests) and Postgres (production); use `m` for the differences.
"""
import importlib
import os
import pkgutil
import re
import time

from pb.config import DATABASE_URL
from pb.db import add_column, sql

NAME_RE = re.compile(r"^(\d{4})_[a-z0-9_]+$")


class Helpers:
    postgres = bool(DATABASE_URL)
    id_col = "SERIAL PRIMARY KEY" if DATABASE_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
    json = "JSONB" if DATABASE_URL else "TEXT"  # read with json.loads either way: see pb.db
    sql = staticmethod(sql)

    def __init__(self, db):
        self.db = db

    def add_column(self, table, column, definition):
        add_column(self.db, table, column, definition)


def available():
    """[(version, module name)] in order."""
    found = []
    for info in pkgutil.iter_modules([os.path.dirname(__file__)]):
        match = NAME_RE.match(info.name)
        if match:
            found.append((match.group(1), info.name))
    versions = [v for v, _ in found]
    duplicates = {v for v in versions if versions.count(v) > 1}
    if duplicates:
        raise RuntimeError(f"Two migrations share a number: {sorted(duplicates)}")
    return sorted(found)


def run_migrations(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, name TEXT NOT NULL, applied_at INTEGER NOT NULL)"
    )
    applied = {row[0] if not isinstance(row, dict) else row["version"] for row in db.execute("SELECT version FROM schema_migrations")}
    helpers = Helpers(db)
    for version, name in available():
        if version in applied:
            continue
        module = importlib.import_module(f"{__name__}.{name}")
        module.up(db, helpers)
        db.execute(
            sql("INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)"),
            (version, name, int(time.time())),
        )
