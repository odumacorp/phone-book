"""The RBAC engine: one place that answers "may this principal do this, here?" for every channel.

Principals: "user:+2547..." (members, operators) and "staff:<id>" (Oduma Corp staff).
Scopes, widest first: platform > tenant > department (and its sub-departments, their groups and members)
> group (and its sub-groups and members). Matter and client scopes cover only themselves.

    can(principal, "announcements.publish", tenant_id, ("group", 12))

A check with no scope asks whether the principal holds the permission for the whole tenant;
can_anywhere() asks whether they hold it anywhere (e.g. to decide whether to show a menu item).
Granting is limited to what the granter holds: grant() refuses any permission or scope they don't have.

This module queries role tables directly (not through TenantRepo) because platform roles have no tenant;
every query still filters by tenant_id explicitly.
"""
import time

from flask import g, has_app_context

from pb.audit import audit
from pb.db import execute, fetch_all, fetch_one, insert_id
from pb.rbac.catalogue import ALL_PERMISSIONS, PLATFORM_PERMISSIONS, SYSTEM_ROLES, TENANT_PERMISSIONS

ROOT = "staff:root"  # logged in with the /admin password (break-glass): has the Platform Super Admin permissions
ROOT_PERMISSIONS = set(SYSTEM_ROLES["platform_super_admin"][2])
SCOPE_TYPES = ("platform", "tenant", "department", "group", "matter", "client")


class PermissionDenied(Exception):
    pass


def user_principal(phone):
    return f"user:{phone}"


def staff_principal(staff_id):
    return f"staff:{staff_id}"


def _cache():
    return g.setdefault("rbac_cache", {}) if has_app_context() else {}


def clear_cache():
    if has_app_context():
        g.pop("rbac_cache", None)


def grants(principal, tenant_id):
    """[(permission, scope_type, scope_id)] the principal holds in this tenant (None = platform)."""
    key = (principal, tenant_id)
    cache = _cache()
    if key not in cache:
        tenant_clause = "ra.tenant_id IS NULL" if tenant_id is None else "ra.tenant_id = ?"
        params = (principal,) if tenant_id is None else (principal, tenant_id)
        rows = fetch_all(
            "SELECT rp.permission, ra.scope_type, ra.scope_id FROM role_assignments ra "
            "JOIN role_permissions rp ON rp.role_id = ra.role_id "
            f"WHERE ra.principal = ? AND {tenant_clause}",
            params,
        )
        if tenant_id is not None:
            # a suspended or closed organisation grants nothing
            tenant = fetch_one("SELECT status FROM tenants WHERE id = ?", (tenant_id,))
            if not tenant or tenant["status"] in ("suspended", "closed"):
                rows = []
        cache[key] = [(r["permission"], r["scope_type"], r["scope_id"]) for r in rows]
    return cache[key]


# ---------------------------------------------------------------- scope hierarchy

def _department_tree(tenant_id, department_id):
    """The department and every department below it."""
    rows = fetch_all("SELECT id, parent_id FROM departments WHERE tenant_id = ?", (tenant_id,))
    return _descendants(department_id, rows)


def _group_tree(tenant_id, group_id):
    rows = fetch_all("SELECT id, parent_id FROM member_groups WHERE tenant_id = ?", (tenant_id,))
    return _descendants(group_id, rows)


def _descendants(root, rows):
    children = {}
    for r in rows:
        children.setdefault(r["parent_id"], []).append(r["id"])
    if root not in {r["id"] for r in rows}:
        return set()
    seen, todo = set(), [root]
    while todo:
        node = todo.pop()
        if node in seen:
            continue
        seen.add(node)
        todo.extend(children.get(node, []))
    return seen


def covers(tenant_id, held, target):
    """Does a grant at scope `held` (type, id) cover the target scope (type, id)?"""
    held_type, held_id = held
    target_type, target_id = target
    if held_type in ("platform", "tenant"):
        return True
    if target_type == "tenant":
        return False
    if held_type in ("matter", "client"):
        return (held_type, held_id) == (target_type, target_id)
    if held_type == "department":
        departments = _department_tree(tenant_id, held_id)
        if target_type == "department":
            return target_id in departments
        if target_type == "group":
            group = fetch_one("SELECT department_id, parent_id FROM member_groups WHERE tenant_id = ? AND id = ?", (tenant_id, target_id))
            while group:
                if group["department_id"] in departments:
                    return True
                group = group["parent_id"] and fetch_one(
                    "SELECT department_id, parent_id FROM member_groups WHERE tenant_id = ? AND id = ?", (tenant_id, group["parent_id"])
                )
            return False
        if target_type == "member":
            if not departments:
                return False
            marks = ", ".join("?" for _ in departments)
            return bool(fetch_one(
                f"SELECT 1 AS x FROM department_members WHERE tenant_id = ? AND membership_id = ? AND department_id IN ({marks})",
                (tenant_id, target_id, *departments),
            ))
        return False
    if held_type == "group":
        groups = _group_tree(tenant_id, held_id)
        if target_type == "group":
            return target_id in groups
        if target_type == "member":
            if not groups:
                return False
            marks = ", ".join("?" for _ in groups)
            return bool(fetch_one(
                f"SELECT 1 AS x FROM group_members WHERE tenant_id = ? AND membership_id = ? AND group_id IN ({marks})",
                (tenant_id, target_id, *groups),
            ))
        return False
    return False


# ---------------------------------------------------------------- checks

def can(principal, permission, tenant_id=None, scope=None):
    if permission not in ALL_PERMISSIONS:
        raise ValueError(f"unknown permission {permission}")
    if permission in PLATFORM_PERMISSIONS:
        if principal == ROOT:
            return permission in ROOT_PERMISSIONS
        return any(p == permission for p, _t, _i in grants(principal, None))
    if tenant_id is None:
        return False
    target = scope or ("tenant", None)
    return any(p == permission and covers(tenant_id, (t, i), target) for p, t, i in grants(principal, tenant_id))


def can_anywhere(principal, permission, tenant_id=None):
    """Holds the permission at any scope (for showing a menu item; the action itself still calls can())."""
    if principal == ROOT and permission in PLATFORM_PERMISSIONS:
        return permission in ROOT_PERMISSIONS
    scope_tenant = None if permission in PLATFORM_PERMISSIONS else tenant_id
    return any(p == permission for p, _t, _i in grants(principal, scope_tenant))


def require(principal, permission, tenant_id=None, scope=None):
    if not can(principal, permission, tenant_id, scope):
        raise PermissionDenied(permission)


def permissions(principal, tenant_id=None):
    if principal == ROOT and tenant_id is None:
        return set(ROOT_PERMISSIONS)
    return {p for p, _t, _i in grants(principal, tenant_id)}


# ---------------------------------------------------------------- roles

def role_by_key(key, tenant_id=None):
    """A tenant's own role with this key, else the system role."""
    if tenant_id is not None:
        row = fetch_one("SELECT id, tenant_id, key, name, scope, is_system FROM roles WHERE tenant_id = ? AND key = ?", (tenant_id, key))
        if row:
            return row
    return fetch_one("SELECT id, tenant_id, key, name, scope, is_system FROM roles WHERE tenant_id IS NULL AND key = ?", (key,))


def role_permissions(role_id):
    return {r["permission"] for r in fetch_all("SELECT permission FROM role_permissions WHERE role_id = ?", (role_id,))}


def roles_for_tenant(tenant_id):
    return fetch_all(
        "SELECT id, tenant_id, key, name, scope, is_system FROM roles WHERE (tenant_id IS NULL AND scope = 'tenant') OR tenant_id = ? "
        "ORDER BY is_system DESC, name",
        (tenant_id,),
    )


def platform_roles():
    return fetch_all("SELECT id, key, name FROM roles WHERE tenant_id IS NULL AND scope = 'platform' ORDER BY id")


def _check_scope(tenant_id, scope):
    scope_type, scope_id = scope
    if scope_type not in SCOPE_TYPES or scope_type == "platform":
        raise ValueError(f"bad scope {scope_type}")
    tables = {"department": "departments", "group": "member_groups"}
    if scope_type in tables and not fetch_one(f"SELECT 1 AS x FROM {tables[scope_type]} WHERE tenant_id = ? AND id = ?", (tenant_id, scope_id)):
        raise ValueError(f"{scope_type} {scope_id} is not in this organisation")


def grant(granter, tenant_id, principal, role_key, scope=("tenant", None), as_platform=False):
    """Give `principal` a role in a tenant at a scope.

    The granter needs roles.manage (tenant.admins.manage for owner/admin roles) covering the scope, and must hold
    every permission in the role at that scope. Platform staff with platform.tenants.manage can grant any
    tenant role (as_platform=True), e.g. to appoint an organisation's first owner.
    """
    role = role_by_key(role_key, tenant_id)
    if not role or role["scope"] != "tenant" or (role["tenant_id"] not in (None, tenant_id)):
        raise ValueError(f"no tenant role {role_key}")
    _check_scope(tenant_id, scope)
    if as_platform:
        if not can(granter, "platform.tenants.manage"):
            raise PermissionDenied("platform.tenants.manage")
    else:
        needed = "tenant.admins.manage" if role_key in ("org_owner", "org_admin") else "roles.manage"
        require(granter, needed, tenant_id, scope)
        missing = [p for p in role_permissions(role["id"]) if not can(granter, p, tenant_id, scope)]
        if missing:
            raise PermissionDenied(f"you don't hold: {', '.join(sorted(missing))}")
    existing = fetch_one(
        "SELECT id FROM role_assignments WHERE tenant_id = ? AND principal = ? AND role_id = ? AND scope_type = ? "
        "AND COALESCE(scope_id, 0) = COALESCE(?, 0)",
        (tenant_id, principal, role["id"], scope[0], scope[1]),
    )
    if existing:
        return existing["id"]
    assignment_id = insert_id(
        "INSERT INTO role_assignments (tenant_id, principal, role_id, scope_type, scope_id, granted_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (tenant_id, principal, role["id"], scope[0], scope[1], granter, int(time.time())),
    )
    clear_cache()
    audit("role.grant", tenant_id, ("principal", principal), {"role": role_key, "scope": list(scope)}, actor=granter)
    return assignment_id


def grant_system(tenant_id, principal, role_key, reason):
    """A role given by the system itself, not a person (e.g. joining an open organisation at sign-up)."""
    role = role_by_key(role_key, tenant_id)
    if not role or role["scope"] != "tenant" or role_key in ("org_owner", "org_admin"):
        raise ValueError("The system only gives ordinary roles.")
    if fetch_one("SELECT 1 AS x FROM role_assignments WHERE tenant_id = ? AND principal = ? AND role_id = ?", (tenant_id, principal, role["id"])):
        return
    insert_id(
        "INSERT INTO role_assignments (tenant_id, principal, role_id, scope_type, scope_id, granted_by, created_at) "
        "VALUES (?, ?, ?, 'tenant', NULL, ?, ?)",
        (tenant_id, principal, role["id"], f"system:{reason}", int(time.time())),
    )
    clear_cache()
    audit("role.grant", tenant_id, ("principal", principal), {"role": role_key, "scope": ["tenant", None], "reason": reason}, actor="system")


def revoke(granter, tenant_id, assignment_id, as_platform=False):
    row = fetch_one(
        "SELECT ra.id, ra.principal, ra.scope_type, ra.scope_id, r.key FROM role_assignments ra JOIN roles r ON r.id = ra.role_id "
        "WHERE ra.tenant_id = ? AND ra.id = ?",
        (tenant_id, assignment_id),
    )
    if not row:
        raise ValueError("no such role assignment")
    scope = (row["scope_type"], row["scope_id"])
    if as_platform:
        if not can(granter, "platform.tenants.manage"):
            raise PermissionDenied("platform.tenants.manage")
    else:
        needed = "tenant.admins.manage" if row["key"] in ("org_owner", "org_admin") else "roles.manage"
        require(granter, needed, tenant_id, scope)
    execute("DELETE FROM role_assignments WHERE tenant_id = ? AND id = ?", (tenant_id, assignment_id))
    clear_cache()
    audit("role.revoke", tenant_id, ("principal", row["principal"]), {"role": row["key"], "scope": list(scope)}, actor=granter)


def role_keys(principal, tenant_id):
    """The keys of the roles a principal holds in a tenant (any scope)."""
    return {r["key"] for r in fetch_all(
        "SELECT r.key FROM role_assignments ra JOIN roles r ON r.id = ra.role_id WHERE ra.tenant_id = ? AND ra.principal = ?",
        (tenant_id, principal),
    )}


def tenant_assignments(tenant_id):
    return fetch_all(
        "SELECT ra.id, ra.principal, ra.scope_type, ra.scope_id, r.key, r.name FROM role_assignments ra "
        "JOIN roles r ON r.id = ra.role_id WHERE ra.tenant_id = ? ORDER BY r.name, ra.principal",
        (tenant_id,),
    )


def create_custom_role(creator, tenant_id, key, name, permission_keys, plan_allows):
    """A tenant's own role. Needs the plan's custom_roles feature, roles.custom, and every permission yourself."""
    if not plan_allows:
        raise PermissionDenied("custom roles aren't included in this organisation's package")
    require(creator, "roles.custom", tenant_id)
    permission_keys = sorted(set(permission_keys))
    unknown = [p for p in permission_keys if p not in TENANT_PERMISSIONS]
    if unknown or not permission_keys:
        raise ValueError(f"unknown permissions: {unknown}" if unknown else "choose at least one permission")
    missing = [p for p in permission_keys if not can(creator, p, tenant_id)]
    if missing:
        raise PermissionDenied(f"you don't hold: {', '.join(missing)}")
    if role_by_key(key, None) or fetch_one("SELECT 1 AS x FROM roles WHERE tenant_id = ? AND key = ?", (tenant_id, key)):
        raise ValueError("a role with this key already exists")
    role_id = insert_id(
        "INSERT INTO roles (tenant_id, key, name, scope, is_system, created_by, created_at) VALUES (?, ?, ?, 'tenant', 0, ?, ?)",
        (tenant_id, key, name, creator, int(time.time())),
    )
    for permission in permission_keys:
        execute("INSERT INTO role_permissions (role_id, permission) VALUES (?, ?)", (role_id, permission))
    audit("role.create", tenant_id, ("role", role_id), {"key": key, "permissions": permission_keys}, actor=creator)
    return role_id


# ---------------------------------------------------------------- platform roles (staff)

def grant_platform(granter, staff_id, role_key):
    role = role_by_key(role_key)
    if not role or role["scope"] != "platform":
        raise ValueError(f"no platform role {role_key}")
    if granter not in (ROOT, "cli"):  # "cli": flask platform create-staff, run by someone with server access
        require(granter, "platform.staff.manage")
        missing = [p for p in role_permissions(role["id"]) if not can(granter, p)]
        if missing:
            raise PermissionDenied(f"you don't hold: {', '.join(sorted(missing))}")
    principal = staff_principal(staff_id)
    if fetch_one("SELECT 1 AS x FROM role_assignments WHERE tenant_id IS NULL AND principal = ? AND role_id = ?", (principal, role["id"])):
        return
    insert_id(
        "INSERT INTO role_assignments (tenant_id, principal, role_id, scope_type, scope_id, granted_by, created_at) "
        "VALUES (NULL, ?, ?, 'platform', NULL, ?, ?)",
        (principal, role["id"], granter, int(time.time())),
    )
    clear_cache()
    audit("platform_role.grant", None, ("staff", staff_id), {"role": role_key}, actor=granter)


def revoke_platform(granter, staff_id, role_key):
    role = role_by_key(role_key)
    if granter != ROOT:
        require(granter, "platform.staff.manage")
    execute(
        "DELETE FROM role_assignments WHERE tenant_id IS NULL AND principal = ? AND role_id = ?",
        (staff_principal(staff_id), role["id"]),
    )
    clear_cache()
    audit("platform_role.revoke", None, ("staff", staff_id), {"role": role_key}, actor=granter)


def staff_roles(staff_id):
    return [r["key"] for r in fetch_all(
        "SELECT r.key FROM role_assignments ra JOIN roles r ON r.id = ra.role_id WHERE ra.tenant_id IS NULL AND ra.principal = ?",
        (staff_principal(staff_id),),
    )]
