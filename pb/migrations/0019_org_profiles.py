"""Organisation profiles: what an organisation tells its members about itself (about, phone, email, website, address)
and its logo (base64, like profile photos). One row per organisation; shown in the app and on the platform."""


def up(db, m):
    db.execute(
        """CREATE TABLE IF NOT EXISTS org_profiles (
               tenant_id INTEGER PRIMARY KEY,
               about TEXT,
               phone TEXT,
               email TEXT,
               website TEXT,
               address TEXT,
               logo_mime TEXT,
               logo_data TEXT,
               updated_at INTEGER NOT NULL DEFAULT 0
           )"""
    )
