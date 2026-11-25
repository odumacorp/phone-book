"""Organisation contacts the admin chose to show automatically in members' own contact lists (always up to date)."""


def up(db, m):
    m.add_column("org_contacts", "in_phone_books", "INTEGER NOT NULL DEFAULT 0")
