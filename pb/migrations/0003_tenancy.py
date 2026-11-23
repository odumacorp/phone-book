"""Organisations (tenants), editions, modules, per-tenant settings, memberships, departments and groups.

Every tenant-owned table has tenant_id NOT NULL and an index starting with tenant_id. Read and write them
through pb.tenancy.repo.TenantRepo, which always filters by tenant.
"""
import json

EDITIONS = [
    # key, name, active, default modules
    ("schools", "Schools", 1, ["members", "directory", "announcements", "emergency", "school_results", "assignments", "guardians"]),
    ("insurance", "Insurance", 1, ["members", "directory", "announcements", "emergency", "client_contacts"]),
    ("legal", "Law firms", 1, ["members", "directory", "announcements", "matters"]),
    ("sacco", "SACCOs", 1, ["members", "directory", "announcements", "emergency", "sacco_finance"]),
    ("company", "Companies", 1, ["members", "directory", "announcements", "emergency", "client_contacts"]),
    # suggested editions: scaffolded, inactive until the Super Admin turns them on
    ("church", "Churches and faith groups", 0, ["members", "directory", "announcements", "emergency"]),
    ("chama", "Chamas and investment groups", 0, ["members", "directory", "announcements", "sacco_finance"]),
    ("health", "Hospitals and clinics", 0, ["members", "directory", "announcements", "emergency"]),
    ("estate", "Residents' and estate associations", 0, ["members", "directory", "announcements", "emergency"]),
    ("county", "County and government offices", 0, ["members", "directory", "announcements", "emergency"]),
]


def up(db, m):
    statements = [
        f"""CREATE TABLE IF NOT EXISTS tenants (
               id {m.id_col},
               name TEXT NOT NULL,
               slug TEXT NOT NULL UNIQUE,
               kind TEXT NOT NULL DEFAULT 'enterprise',
               edition TEXT,
               plan_key TEXT,
               status TEXT NOT NULL DEFAULT 'trial',
               org_code TEXT UNIQUE,
               language TEXT NOT NULL DEFAULT 'en',
               reseller_id INTEGER,
               created_by TEXT,
               created_at INTEGER NOT NULL,
               updated_at INTEGER NOT NULL
           )""",
        """CREATE TABLE IF NOT EXISTS editions (
               key TEXT PRIMARY KEY,
               name TEXT NOT NULL,
               active INTEGER NOT NULL DEFAULT 1,
               modules TEXT NOT NULL,
               sort INTEGER NOT NULL DEFAULT 0
           )""",
        """CREATE TABLE IF NOT EXISTS tenant_modules (
               tenant_id INTEGER NOT NULL,
               module_key TEXT NOT NULL,
               enabled INTEGER NOT NULL DEFAULT 1,
               source TEXT NOT NULL DEFAULT 'edition',
               updated_at INTEGER NOT NULL,
               PRIMARY KEY (tenant_id, module_key)
           )""",
        # Operator options and Super Admin overrides for one tenant (e.g. allow_other_phone, otp)
        """CREATE TABLE IF NOT EXISTS tenant_settings (
               tenant_id INTEGER NOT NULL,
               key TEXT NOT NULL,
               value TEXT NOT NULL,
               PRIMARY KEY (tenant_id, key)
           )""",
        # A person (by phone number) in an organisation. They may not have registered an account yet.
        f"""CREATE TABLE IF NOT EXISTS memberships (
               id {m.id_col},
               tenant_id INTEGER NOT NULL,
               user_phone TEXT NOT NULL,
               display_name TEXT,
               member_no TEXT,
               status TEXT NOT NULL DEFAULT 'active',
               joined_at INTEGER NOT NULL,
               left_at INTEGER,
               UNIQUE (tenant_id, user_phone)
           )""",
        "CREATE INDEX IF NOT EXISTS idx_memberships_phone ON memberships(user_phone)",
        f"""CREATE TABLE IF NOT EXISTS departments (
               id {m.id_col},
               tenant_id INTEGER NOT NULL,
               name TEXT NOT NULL,
               parent_id INTEGER,
               created_at INTEGER NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_departments_tenant ON departments(tenant_id)",
        # Classes, streams, fellowships, client segments...
        f"""CREATE TABLE IF NOT EXISTS member_groups (
               id {m.id_col},
               tenant_id INTEGER NOT NULL,
               kind TEXT NOT NULL DEFAULT 'custom',
               name TEXT NOT NULL,
               parent_id INTEGER,
               department_id INTEGER,
               created_at INTEGER NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_member_groups_tenant ON member_groups(tenant_id)",
        """CREATE TABLE IF NOT EXISTS group_members (
               tenant_id INTEGER NOT NULL,
               group_id INTEGER NOT NULL,
               membership_id INTEGER NOT NULL,
               role_in_group TEXT,
               PRIMARY KEY (tenant_id, group_id, membership_id)
           )""",
        """CREATE TABLE IF NOT EXISTS department_members (
               tenant_id INTEGER NOT NULL,
               department_id INTEGER NOT NULL,
               membership_id INTEGER NOT NULL,
               PRIMARY KEY (tenant_id, department_id, membership_id)
           )""",
    ]
    for statement in statements:
        db.execute(statement)
    m.add_column("users", "language", "TEXT NOT NULL DEFAULT 'en'")
    m.add_column("users", "status", "TEXT NOT NULL DEFAULT 'active'")
    for sort, (key, name, active, modules) in enumerate(EDITIONS):
        db.execute(
            m.sql("INSERT INTO editions (key, name, active, modules, sort) VALUES (?, ?, ?, ?, ?) ON CONFLICT (key) DO NOTHING"),
            (key, name, active, json.dumps(modules), sort),
        )
