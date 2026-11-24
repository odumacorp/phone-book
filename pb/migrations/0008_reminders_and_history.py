"""Reminders (SMS and in-app, now or scheduled) and history: deleted notices and contacts are kept, not erased."""


def up(db, m):
    for table in ("notices", "org_contacts"):
        m.add_column(table, "deleted_at", "INTEGER")
        m.add_column(table, "deleted_by", "TEXT")
    db.execute(
        f"""CREATE TABLE IF NOT EXISTS reminders (
               id {m.id_col},
               tenant_id INTEGER NOT NULL,
               title TEXT NOT NULL,
               body TEXT NOT NULL,
               channels TEXT NOT NULL,
               audience TEXT NOT NULL DEFAULT '',
               placement TEXT NOT NULL DEFAULT '',
               notice_id INTEGER,
               send_at INTEGER NOT NULL,
               status TEXT NOT NULL DEFAULT 'scheduled',
               recipients INTEGER NOT NULL DEFAULT 0,
               app_sent INTEGER NOT NULL DEFAULT 0,
               sms_sent INTEGER NOT NULL DEFAULT 0,
               sms_parts INTEGER NOT NULL DEFAULT 0,
               note TEXT,
               created_by TEXT,
               created_at INTEGER NOT NULL,
               sent_at INTEGER,
               deleted_at INTEGER,
               deleted_by TEXT
           )"""
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, send_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_reminders_tenant ON reminders(tenant_id, created_at)")
