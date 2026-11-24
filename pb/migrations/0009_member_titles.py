"""A job title for each member (e.g. Bursar, Class teacher). Used on the members list and in "use from another phone" rules."""


def up(db, m):
    m.add_column("memberships", "title", "TEXT")
