"""How someone became a member: added by the organisation, or signed up and chose it ("signup")."""


def up(db, m):
    m.add_column("memberships", "joined_via", "TEXT")
