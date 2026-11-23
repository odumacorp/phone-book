"""What organisation admins and operators publish for their members: contacts (incl. emergency) and notices.

audience: "" = everyone in the organisation, else a JSON list of role keys (e.g. ["client"]) that may see it.
"""


def up(db, m):
    statements = [
        f"""CREATE TABLE IF NOT EXISTS org_contacts (
               id {m.id_col},
               tenant_id INTEGER NOT NULL,
               name TEXT NOT NULL,
               phone TEXT NOT NULL,
               title TEXT,
               group_name TEXT,
               email TEXT,
               is_emergency INTEGER NOT NULL DEFAULT 0,
               audience TEXT NOT NULL DEFAULT '',
               sort INTEGER NOT NULL DEFAULT 0,
               created_by TEXT,
               created_at INTEGER NOT NULL,
               updated_at INTEGER NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_org_contacts_tenant ON org_contacts(tenant_id, is_emergency)",
        f"""CREATE TABLE IF NOT EXISTS notices (
               id {m.id_col},
               tenant_id INTEGER NOT NULL,
               title TEXT NOT NULL,
               body TEXT NOT NULL,
               is_emergency INTEGER NOT NULL DEFAULT 0,
               pinned INTEGER NOT NULL DEFAULT 0,
               audience TEXT NOT NULL DEFAULT '',
               publish_at INTEGER NOT NULL,
               expires_at INTEGER,
               created_by TEXT,
               created_at INTEGER NOT NULL,
               updated_at INTEGER NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_notices_tenant ON notices(tenant_id, publish_at)",
    ]
    for statement in statements:
        db.execute(statement)
