"""Contacts an organisation puts into its members' personal phone books (source_tenant = that organisation)."""


def up(db, m):
    m.add_column("contacts", "source_tenant", "INTEGER")
