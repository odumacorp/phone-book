"""People arrange their own contacts: contacts.sort is the position they chose (NULL: not arranged, listed after the
arranged ones, priority first then A-Z). Organisation contacts already have org_contacts.sort (0 = not arranged)."""


def up(db, m):
    m.add_column("contacts", "sort", "INTEGER")
