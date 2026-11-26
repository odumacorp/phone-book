"""What the organisation dashboard needs, recorded from now on.

- ussd_sessions.reached_contact: 1 once the member reached a contact (list, search result or an organisation's contacts);
  a session that ends without it is a drop-off.
- ussd_failures: USSD requests that failed (kind "error") or were too slow for the network ("timeout").
- lookups: each time someone opens contacts, by channel ("ussd", "android", "ios", "web"); tenant_id when it was an
  organisation's contacts.
- deliveries: one row per member for each notice or reminder: sent, delivered (SMS: Africa's Talking delivery report;
  app and notice board: at once) and viewed (opened on USSD or in the app). provider_id is the SMS message id.
"""


def up(db, m):
    m.add_column("ussd_sessions", "reached_contact", "INTEGER NOT NULL DEFAULT 0")
    db.execute(f"""CREATE TABLE IF NOT EXISTS ussd_failures (
                     id {m.id_col}, caller TEXT, kind TEXT NOT NULL, created_at INTEGER NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_ussd_failures_time ON ussd_failures(created_at)")
    db.execute(f"""CREATE TABLE IF NOT EXISTS lookups (
                     id {m.id_col}, owner TEXT NOT NULL, tenant_id INTEGER, channel TEXT NOT NULL, kind TEXT NOT NULL,
                     created_at INTEGER NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_lookups_time ON lookups(created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_lookups_owner ON lookups(owner, created_at)")
    db.execute(f"""CREATE TABLE IF NOT EXISTS deliveries (
                     id {m.id_col}, tenant_id INTEGER NOT NULL, kind TEXT NOT NULL, item_id INTEGER NOT NULL,
                     owner TEXT NOT NULL, channel TEXT NOT NULL, provider_id TEXT, status TEXT NOT NULL DEFAULT 'sent',
                     sent_at INTEGER NOT NULL, delivered_at INTEGER, viewed_at INTEGER)""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_deliveries_tenant ON deliveries(tenant_id, sent_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_deliveries_item ON deliveries(tenant_id, kind, item_id, owner)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_deliveries_provider ON deliveries(provider_id)")
