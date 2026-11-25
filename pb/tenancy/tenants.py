"""Organisations (tenants): creation from an edition preset, status, modules, settings and members.

Platform-level reads (the list of all tenants) query `tenants` directly; everything owned by one tenant
goes through TenantRepo.
"""
import json
import re
import time

from pb.audit import audit
from pb.billing.catalogue import plan, plan_feature
from pb.db import fetch_all, fetch_one, execute, insert_id
from pb.features import feature_on
from pb.rbac import engine as rbac
from pb.tenancy.modules import MODULES, is_core
from pb.tenancy.repo import TenantRepo
from pb.text import login_phone

STATUSES = {
    "trial": "Trial", "active": "Active", "grace": "Grace period (unpaid)", "suspended": "Suspended (data kept, access blocked)",
    "closed": "Closed",
}
# Operator options, with defaults. Values are strings in tenant_settings.
SETTINGS = {
    "allow_other_phone": ("1", "Members can use USSD from another phone (phone number + PIN). Off: only from their own registered number."),
    "otp": ("inherit", "OTP by SMS for sensitive USSD views: inherit (the platform switch), on, or off."),
    "language": ("en", "Default language for members: en or sw."),
    "member_contacts": ("own", "Members' own phone books: own (they add and sync contacts) or managed (the organisation provides them)."),
    "join_mode": ("approval", "Joining at sign-up: open (anyone joins), approval (requests need approval) or closed (invite only)."),
}
SETTING_CHOICES = {"member_contacts": ("own", "managed"), "join_mode": ("open", "approval", "closed"), "allow_other_phone": ("1", "0", "some"), "otp": ("inherit", "on", "off"), "language": ("en", "sw")}


def slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "org"
    base, n = slug, 2
    while fetch_one("SELECT 1 AS x FROM tenants WHERE slug = ?", (slug,)):
        slug, n = f"{base}-{n}", n + 1
    return slug


def editions(active_only=False):
    clause = "WHERE active = 1" if active_only else ""
    return [dict(r, modules=json.loads(r["modules"])) for r in fetch_all(f"SELECT key, name, active, modules, sort FROM editions {clause} ORDER BY sort")]


def edition(key):
    row = fetch_one("SELECT key, name, active, modules FROM editions WHERE key = ?", (key,))
    return dict(row, modules=json.loads(row["modules"])) if row else None


def set_edition_active(actor, key, active):
    execute("UPDATE editions SET active = ? WHERE key = ?", (1 if active else 0, key))
    audit("edition.set_active", None, ("edition", key), {"active": bool(active)}, actor=actor)


def tenant(tenant_id):
    return fetch_one(
        "SELECT id, name, slug, kind, edition, plan_key, status, org_code, language, reseller_id, created_by, created_at, updated_at "
        "FROM tenants WHERE id = ?",
        (tenant_id,),
    )


def tenants(status=None, reseller_id=None, query=None):
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if reseller_id is not None:
        where.append("reseller_id = ?")
        params.append(reseller_id)
    if query:
        where.append("(LOWER(name) LIKE ? OR org_code = ?)")
        params += [f"%{query.lower()}%", query]
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return fetch_all(
        "SELECT id, name, slug, edition, plan_key, status, org_code, reseller_id, created_at, "
        "(SELECT COUNT(*) FROM memberships m WHERE m.tenant_id = tenants.id AND m.status = 'active') AS members "
        f"FROM tenants {clause} ORDER BY created_at DESC",
        tuple(params),
    )


def create_tenant(actor, name, edition_key, plan_key, reseller_id=None):
    name = " ".join(str(name or "").split())[:80]
    if not name:
        raise ValueError("Enter the organisation's name.")
    ed = edition(edition_key)
    if not ed:
        raise ValueError("Choose an edition.")
    if not ed["active"]:
        raise ValueError(f"The {ed['name']} edition isn't available yet (turn it on in Settings).")
    p = plan(plan_key)
    if not p or p["audience"] != "enterprise":
        raise ValueError("Choose an enterprise plan.")
    now = int(time.time())
    tenant_id = insert_id(
        "INSERT INTO tenants (name, slug, kind, edition, plan_key, status, language, reseller_id, created_by, created_at, updated_at) "
        "VALUES (?, ?, 'enterprise', ?, ?, 'trial', 'en', ?, ?, ?, ?)",
        (name, slugify(name), edition_key, plan_key, reseller_id, actor, now, now),
    )
    apply_edition(actor, tenant_id, edition_key, plan_key, audit_it=False)
    audit("tenant.create", tenant_id, ("tenant", tenant_id), {"name": name, "edition": edition_key, "plan": plan_key,
                                                               "reseller_id": reseller_id}, actor=actor)
    return tenant_id


def apply_edition(actor, tenant_id, edition_key, plan_key, audit_it=True):
    """Switch on the edition's modules. Edition modules need a plan that includes them; otherwise they're added
    switched off (source "addon"), waiting for the add-on to be bought or the Super Admin to switch them on."""
    repo = TenantRepo(tenant_id)
    included = plan_feature(plan_key, "edition_modules", "addon") == "included"
    now = int(time.time())
    for key in edition(edition_key)["modules"]:
        enabled = is_core(key) or included
        repo.upsert("tenant_modules", ["module_key"], module_key=key, enabled=1 if enabled else 0,
                    source="edition" if enabled else "addon", updated_at=now)
    if audit_it:
        audit("tenant.apply_edition", tenant_id, ("tenant", tenant_id), {"edition": edition_key, "plan": plan_key}, actor=actor)


def update_tenant(actor, tenant_id, **values):
    old = tenant(tenant_id)
    if not old:
        raise ValueError("No such organisation.")
    values = {k: v for k, v in values.items() if k in {"name", "plan_key", "status", "edition", "reseller_id"}}
    if "status" in values and values["status"] not in STATUSES:
        raise ValueError("Unknown status.")
    if "plan_key" in values:
        p = plan(values["plan_key"])
        if not p or p["audience"] != "enterprise":
            raise ValueError("Choose an enterprise plan.")
    changes = {k: [old[k], v] for k, v in values.items() if old[k] != v}
    if changes:
        sets = ", ".join(f"{k} = ?" for k in values)
        execute(f"UPDATE tenants SET {sets}, updated_at = ? WHERE id = ?", (*values.values(), int(time.time()), tenant_id))
        rbac.clear_cache()
        audit("tenant.update", tenant_id, ("tenant", tenant_id), changes, actor=actor)
    return changes


# ---------------------------------------------------------------- modules (feature flags per tenant)

def tenant_modules(tenant_id):
    rows = {r["module_key"]: r for r in TenantRepo(tenant_id).all("tenant_modules")}
    return [
        {"key": key, "name": name, "core": core, "description": desc,
         "enabled": bool(rows[key]["enabled"]) if key in rows else False, "source": rows[key]["source"] if key in rows else None}
        for key, (name, core, desc) in MODULES.items()
    ]


def module_enabled(tenant_id, module_key):
    """The backend check every module makes (USSD menus, API and portal only show what this allows)."""
    t = tenant(tenant_id)
    if not t or t["status"] in ("suspended", "closed"):
        return False
    row = TenantRepo(tenant_id).one("tenant_modules", "module_key = ?", (module_key,))
    return bool(row and row["enabled"])


def set_module(actor, tenant_id, module_key, enabled):
    if module_key not in MODULES:
        raise ValueError("Unknown module.")
    TenantRepo(tenant_id).upsert("tenant_modules", ["module_key"], module_key=module_key, enabled=1 if enabled else 0,
                                 source="manual", updated_at=int(time.time()))
    audit("tenant.module", tenant_id, ("module", module_key), {"enabled": bool(enabled)}, actor=actor)


def plan_allows(tenant_id, feature):
    t = tenant(tenant_id)
    return bool(t and t["plan_key"] and plan_feature(t["plan_key"], feature, "0") not in ("0", None))


# ---------------------------------------------------------------- settings

def get_setting(tenant_id, key):
    row = TenantRepo(tenant_id).one("tenant_settings", "key = ?", (key,))
    return row["value"] if row else SETTINGS[key][0]


def settings(tenant_id):
    return {key: get_setting(tenant_id, key) for key in SETTINGS}


def set_setting(actor, tenant_id, key, value):
    if key not in SETTINGS or value not in SETTING_CHOICES[key]:
        raise ValueError("Invalid setting.")
    old = get_setting(tenant_id, key)
    TenantRepo(tenant_id).upsert("tenant_settings", ["key"], key=key, value=value)
    if old != value:
        audit("tenant.setting", tenant_id, ("setting", key), {key: [old, value]}, actor=actor)


def other_phone_rules(tenant_id):
    """Who may use USSD from another phone when allow_other_phone is "some": roles, departments (ids) and titles."""
    row = TenantRepo(tenant_id).one("tenant_settings", "key = ?", ("other_phone_rules",))
    try:
        rules = json.loads(row["value"]) if row else {}
    except ValueError:
        rules = {}
    return {"roles": rules.get("roles") or [], "departments": rules.get("departments") or [], "titles": rules.get("titles") or []}


def set_other_phone_rules(actor, tenant_id, mode, roles=(), departments=(), titles=()):
    """mode: "1" everyone, "0" own phone only, "some" only people matching any of the roles, departments or titles."""
    if mode not in ("1", "0", "some"):
        raise ValueError("Choose who can use another phone.")
    rules = {
        "roles": sorted({r for r in roles if r}),
        "departments": sorted({int(d) for d in departments if str(d).isdigit()}),
        "titles": sorted({" ".join(t.split())[:60] for t in titles if t and t.strip()}, key=str.lower),
    }
    if mode == "some" and not any(rules.values()):
        raise ValueError("Pick at least one role, department or title, or choose another option.")
    old = (get_setting(tenant_id, "allow_other_phone"), other_phone_rules(tenant_id))
    repo = TenantRepo(tenant_id)
    repo.upsert("tenant_settings", ["key"], key="allow_other_phone", value=mode)
    repo.upsert("tenant_settings", ["key"], key="other_phone_rules", value=json.dumps(rules))
    if old != (mode, rules):
        audit("tenant.other_phone_rules", tenant_id, ("setting", "allow_other_phone"),
              {"mode": [old[0], mode], "rules": rules}, actor=actor)


def allow_other_phone(tenant_id, phone=None):
    """May this member (or, without a phone, members in general) use the organisation from a phone that isn't theirs?"""
    mode = get_setting(tenant_id, "allow_other_phone")
    if mode != "some":
        return mode == "1"
    if not phone:
        return False
    repo = TenantRepo(tenant_id)
    member = repo.one("memberships", "user_phone = ? AND status = 'active'", (phone,))
    if not member:
        return False
    rules = other_phone_rules(tenant_id)
    roles = rbac.role_keys(rbac.user_principal(phone), tenant_id) or {"member"}
    if roles & set(rules["roles"]):
        return True
    if member["title"] and member["title"].lower() in {t.lower() for t in rules["titles"]}:
        return True
    if rules["departments"]:
        allowed = set()
        for d in rules["departments"]:
            allowed |= rbac._department_tree(tenant_id, d)
        if any(r["department_id"] in allowed for r in repo.all("department_members", "membership_id = ?", (member["id"],))):
            return True
    return False


def otp_on_for(tenant_id):
    """OTP for this tenant's sensitive USSD views: its own on/off, else the platform switch."""
    value = get_setting(tenant_id, "otp")
    return feature_on("otp") if value == "inherit" else value == "on"


# ---------------------------------------------------------------- members

def add_member(actor, tenant_id, raw_phone, display_name=None, member_no=None):
    phone = login_phone(str(raw_phone or ""))
    if not phone:
        raise ValueError("Enter a Kenyan mobile number (07..., 01... or +254...).")
    repo = TenantRepo(tenant_id)
    existing = repo.one("memberships", "user_phone = ?", (phone,))
    if existing:
        if existing["status"] != "active":
            repo.update("memberships", "id = ?", (existing["id"],), status="active", left_at=None)
        return existing["id"]
    membership_id = repo.insert("memberships", user_phone=phone, display_name=(display_name or "").strip()[:80] or None,
                                member_no=(member_no or "").strip()[:40] or None, status="active", joined_at=int(time.time()))
    audit("member.add", tenant_id, ("member", membership_id), {"phone": phone}, actor=actor)
    return membership_id


def members(tenant_id, limit=500):
    return TenantRepo(tenant_id).all("memberships", order="display_name, user_phone", limit=limit)


def memberships_of(phone):
    """The organisations a phone number belongs to (across tenants: the person's own view)."""
    return fetch_all(
        "SELECT m.id, m.tenant_id, t.name, t.status FROM memberships m JOIN tenants t ON t.id = m.tenant_id "
        "WHERE m.user_phone = ? AND m.status = 'active' ORDER BY t.name",
        (phone,),
    )


SUPER_ADMIN, ADMIN = "org_owner", "org_admin"  # every organisation has exactly one Super Admin; Admins are optional


def super_admin(tenant_id):
    """The principal of the organisation's Super Admin, or None."""
    owners = [a["principal"] for a in rbac.tenant_assignments(tenant_id) if a["key"] == SUPER_ADMIN]
    return owners[0] if owners else None


def admin_limit(tenant_id):
    """How many Admins the plan allows (None = no limit). The Super Admin isn't counted."""
    t = tenant(tenant_id)
    value = plan_feature(t["plan_key"], "max_admins", "") if t and t["plan_key"] else ""
    return int(value) if str(value).isdigit() else None


def admins(tenant_id):
    return sorted({a["principal"] for a in rbac.tenant_assignments(tenant_id) if a["key"] == ADMIN})


def check_admin_room(tenant_id, principal):
    """Raise if giving `principal` the Admin role would go over the plan's limit."""
    limit = admin_limit(tenant_id)
    current = admins(tenant_id)
    if limit is not None and principal not in current and len(current) >= limit:
        raise ValueError(f"This organisation's plan allows {limit} admin{'' if limit == 1 else 's'} "
                         f"(besides the Super Admin). Remove an admin first, or upgrade the plan.")


def appoint_owner(actor, tenant_id, raw_phone, display_name=None, as_platform=True):
    """Make someone the organisation's Super Admin. If there already is one, the role moves: the previous
    Super Admin becomes an Admin (or a Member if the plan has no admin room left)."""
    phone = login_phone(str(raw_phone or ""))
    if not phone:
        raise ValueError("Enter the Super Admin's Kenyan mobile number.")
    add_member(actor, tenant_id, phone, display_name)
    new = rbac.user_principal(phone)
    previous = super_admin(tenant_id)
    if previous == new:
        return None
    assignment = rbac.grant(actor, tenant_id, new, SUPER_ADMIN, as_platform=as_platform)
    for a in rbac.tenant_assignments(tenant_id):  # the new Super Admin doesn't need their old roles
        if a["principal"] == new and a["key"] != SUPER_ADMIN:
            rbac.revoke(actor, tenant_id, a["id"], as_platform=as_platform)
    if previous:
        try:
            check_admin_room(tenant_id, previous)
            fallback = ADMIN
        except ValueError:
            fallback = "member"
        rbac.grant(actor, tenant_id, previous, fallback, as_platform=as_platform)
        for a in rbac.tenant_assignments(tenant_id):
            if a["principal"] == previous and a["key"] == SUPER_ADMIN:
                rbac.revoke(actor, tenant_id, a["id"], as_platform=as_platform)
        audit("tenant.super_admin_change", tenant_id, ("principal", new),
              {"from": previous, "to": new, "previous_now": fallback}, actor=actor)
    return assignment


# ---------------------------------------------------------------- roles and titles offered in this organisation

_STAFF = ["content_publisher", "department_supervisor", "org_admin", "auditor"]
EDITION_ROLES = {  # roles that make sense for each kind of organisation (custom roles are always offered too)
    "schools": ["member", "guardian", "teacher", *_STAFF],
    "insurance": ["member", "client", *_STAFF],
    "legal": ["member", "client", *_STAFF],
    "company": ["member", "client", *_STAFF],
    "sacco": ["member", *_STAFF],
    "chama": ["member", *_STAFF],
    "church": ["member", *_STAFF],
    "health": ["member", "client", *_STAFF],
    "estate": ["member", *_STAFF],
    "county": ["member", *_STAFF],
}
DEFAULT_ROLES = ["member", "client", "guardian", "teacher", *_STAFF]


def available_roles(tenant_id):
    """[(key, name)] of the roles people can be given here: the ones for this kind of organisation, then its own."""
    t = tenant(tenant_id)
    wanted = EDITION_ROLES.get(t["edition"] if t else None, DEFAULT_ROLES)
    roles = rbac.roles_for_tenant(tenant_id)
    system = {r["key"]: r["name"] for r in roles if r["is_system"]}
    own = [(r["key"], r["name"]) for r in roles if not r["is_system"] and r["tenant_id"] == tenant_id]
    return [(k, system[k]) for k in wanted if k in system] + own


def role_offered(tenant_id, role_key):
    return role_key == SUPER_ADMIN or role_key in dict(available_roles(tenant_id))


def org_titles(tenant_id):
    """The organisation's own list of job titles (added by its admins)."""
    row = TenantRepo(tenant_id).one("tenant_settings", "key = ?", ("titles",))
    try:
        return json.loads(row["value"]) if row else []
    except ValueError:
        return []


def set_org_titles(actor, tenant_id, titles):
    clean = []
    for t in titles:
        t = " ".join(str(t or "").split())[:60]
        if t and t.lower() not in {c.lower() for c in clean}:
            clean.append(t)
    old = org_titles(tenant_id)
    TenantRepo(tenant_id).upsert("tenant_settings", ["key"], key="titles", value=json.dumps(clean))
    if old != clean:
        audit("tenant.titles", tenant_id, ("setting", "titles"), {"added": [t for t in clean if t not in old],
                                                                    "removed": [t for t in old if t not in clean]}, actor=actor)
    return clean
