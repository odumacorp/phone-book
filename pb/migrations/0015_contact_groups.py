"""Contact groups: named sets of organisation contacts that the admin gives to departments, groups or roles.

Everyone they're given to finds the group's contacts in their own contact list.
audience: "" = everyone in the organisation, else roles / "d:<id>" / "g:<id>" (like other audiences).
"""


def up(db, m):
    db.execute(
        f"""CREATE TABLE IF NOT EXISTS contact_groups (
               id {m.id_col},
               tenant_id INTEGER NOT NULL,
               name TEXT NOT NULL,
               audience TEXT,
               created_at INTEGER NOT NULL
           )"""
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_contact_groups_tenant ON contact_groups(tenant_id)")
    db.execute(
        """CREATE TABLE IF NOT EXISTS contact_group_items (
               tenant_id INTEGER NOT NULL,
               group_id INTEGER NOT NULL,
               contact_id INTEGER NOT NULL,
               PRIMARY KEY (tenant_id, group_id, contact_id)
           )"""
    )
