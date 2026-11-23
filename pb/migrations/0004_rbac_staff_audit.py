"""Roles and permissions, platform staff accounts and the audit log.

Permissions are a catalogue in code (pb/rbac/catalogue.py). System roles are synced from it at every
startup (pb/rbac/catalogue.py: sync_system_roles), so changing a system role needs no migration.
"""


def up(db, m):
    statements = [
        # tenant_id NULL = a system role (same for every tenant, or a platform role)
        f"""CREATE TABLE IF NOT EXISTS roles (
               id {m.id_col},
               tenant_id INTEGER,
               key TEXT NOT NULL,
               name TEXT NOT NULL,
               scope TEXT NOT NULL DEFAULT 'tenant',
               is_system INTEGER NOT NULL DEFAULT 0,
               created_by TEXT,
               created_at INTEGER NOT NULL
           )""",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_roles_key ON roles(COALESCE(tenant_id, 0), key)",
        """CREATE TABLE IF NOT EXISTS role_permissions (
               role_id INTEGER NOT NULL,
               permission TEXT NOT NULL,
               PRIMARY KEY (role_id, permission)
           )""",
        # Who holds which role, where. principal: "user:+2547..." or "staff:<id>".
        # tenant_id NULL only for platform roles. scope_type: platform|tenant|department|group|matter|client
        f"""CREATE TABLE IF NOT EXISTS role_assignments (
               id {m.id_col},
               tenant_id INTEGER,
               principal TEXT NOT NULL,
               role_id INTEGER NOT NULL,
               scope_type TEXT NOT NULL,
               scope_id INTEGER,
               granted_by TEXT,
               created_at INTEGER NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_role_assignments_principal ON role_assignments(principal, tenant_id)",
        "CREATE INDEX IF NOT EXISTS idx_role_assignments_tenant ON role_assignments(tenant_id)",
        f"""CREATE TABLE IF NOT EXISTS staff_users (
               id {m.id_col},
               email TEXT NOT NULL UNIQUE,
               name TEXT NOT NULL,
               password_hash TEXT NOT NULL,
               active INTEGER NOT NULL DEFAULT 1,
               failed_attempts INTEGER NOT NULL DEFAULT 0,
               locked_until INTEGER NOT NULL DEFAULT 0,
               last_login INTEGER,
               created_by TEXT,
               created_at INTEGER NOT NULL
           )""",
        # Append-only: every permission change, admin action and access to sensitive data
        f"""CREATE TABLE IF NOT EXISTS audit_log (
               id {m.id_col},
               tenant_id INTEGER,
               actor TEXT NOT NULL,
               action TEXT NOT NULL,
               target_type TEXT,
               target_id TEXT,
               details TEXT,
               ip TEXT,
               channel TEXT,
               created_at INTEGER NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_audit_log_time ON audit_log(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_audit_log_tenant ON audit_log(tenant_id, created_at)",
    ]
    for statement in statements:
        db.execute(statement)
