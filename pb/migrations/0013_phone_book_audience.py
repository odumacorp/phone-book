"""Who gets an organisation contact in their own contact list: "" = everyone who can see it, else roles/departments/groups."""


def up(db, m):
    m.add_column("org_contacts", "phone_book_audience", "TEXT NOT NULL DEFAULT ''")
