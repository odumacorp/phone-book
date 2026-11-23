"""Background jobs (pb/jobs.py): when each one last ran and how it went."""


def up(db, m):
    db.execute(
        """CREATE TABLE IF NOT EXISTS job_runs (
               name TEXT PRIMARY KEY,
               last_started INTEGER NOT NULL DEFAULT 0,
               last_finished INTEGER,
               last_status TEXT,
               last_error TEXT
           )"""
    )
