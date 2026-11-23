"""Organisation content: contacts (including emergency contacts) and notices that admins/operators publish,
and what each member is allowed to see of them.

Who may manage: contacts.manage (contacts), emergency.manage (emergency contacts), announcements.publish (notices),
held for the whole organisation. Who may see: active members whose roles match the item's audience
("" = everyone in the organisation). The same functions serve USSD, the app API and the portal.
"""
import json
import time

from pb.audit import audit
from pb.rbac import engine as rbac
from pb.tenancy import tenants as tenancy
from pb.tenancy.repo import TenantRepo
from pb.text import normalize_contact_phone

NAME_MAX, TITLE_MAX, GROUP_MAX = 60, 60, 40
NOTICE_TITLE_MAX, NOTICE_BODY_MAX = 80, 2000


class NotAllowed(Exception):
    pass


# ---------------------------------------------------------------- audience

def parse_audience(roles, tenant_id):
    """Form values -> stored audience. Empty (or "everyone") -> "" (everyone)."""
    roles = [r for r in (roles or []) if r and r != "everyone"]
    if not roles:
        return ""
    valid = {r["key"] for r in rbac.roles_for_tenant(tenant_id)}
    unknown = [r for r in roles if r not in valid]
    if unknown:
        raise ValueError(f"Unknown role: {', '.join(unknown)}")
    return json.dumps(sorted(set(roles)))


def audience_roles(stored):
    return json.loads(stored) if stored else []


def viewer_roles(tenant_id, phone):
    """The role keys a person holds in an organisation, or None if they aren't an active member.
    A member without any role counts as "member"."""
    membership = TenantRepo(tenant_id).one("memberships", "user_phone = ? AND status = 'active'", (phone,))
    if not membership:
        return None
    keys = rbac.role_keys(rbac.user_principal(phone), tenant_id)
    return keys or {"member"}


def _visible(item, roles):
    allowed = audience_roles(item["audience"])
    return not allowed or bool(set(allowed) & roles)


def my_organisations(phone):
    """Organisations where this person is an active member and the organisation is usable."""
    return [o for o in tenancy.memberships_of(phone) if o["status"] not in ("suspended", "closed")]


# ---------------------------------------------------------------- managing (portal)

def can_manage(principal, tenant_id, permission):
    return rbac.can(principal, permission, tenant_id) or rbac.can(principal, "platform.tenants.manage")


def _require(principal, tenant_id, permission):
    if not can_manage(principal, tenant_id, permission):
        raise NotAllowed("You don't have permission to do that here.")


def contacts(tenant_id, emergency=None):
    where, params = "", ()
    if emergency is not None:
        where, params = "is_emergency = ?", (1 if emergency else 0,)
    return TenantRepo(tenant_id).all("org_contacts", where, params, order="is_emergency DESC, sort, group_name, name")


def contact(tenant_id, contact_id):
    return TenantRepo(tenant_id).one("org_contacts", "id = ?", (contact_id,))


def _clean_contact(form, tenant_id):
    name = " ".join(str(form.get("name") or "").split())[:NAME_MAX]
    phone = normalize_contact_phone(str(form.get("phone") or ""))
    if not name:
        raise ValueError("Enter the contact's name.")
    if not phone:
        raise ValueError("Enter a valid phone number (e.g. 0712 345 678, or a short code like 999).")
    return {
        "name": name,
        "phone": phone,
        "title": " ".join(str(form.get("title") or "").split())[:TITLE_MAX] or None,
        "group_name": " ".join(str(form.get("group_name") or "").split())[:GROUP_MAX] or None,
        "email": str(form.get("email") or "").strip()[:120] or None,
        "audience": parse_audience(form.get("audience"), tenant_id),
    }


def save_contact(principal, tenant_id, form, emergency, contact_id=None):
    _require(principal, tenant_id, "emergency.manage" if emergency else "contacts.manage")
    values = _clean_contact(form, tenant_id)
    repo, now = TenantRepo(tenant_id), int(time.time())
    if contact_id:
        if not repo.update("org_contacts", "id = ?", (contact_id,), updated_at=now, **values):
            raise ValueError("That contact no longer exists.")
        audit("org_contact.update", tenant_id, ("org_contact", contact_id), {"name": values["name"]}, actor=principal)
        return contact_id
    new_id = repo.insert("org_contacts", is_emergency=1 if emergency else 0, created_by=principal, created_at=now, updated_at=now, **values)
    audit("org_contact.add", tenant_id, ("org_contact", new_id), {"name": values["name"], "emergency": bool(emergency)}, actor=principal)
    return new_id


def delete_contact(principal, tenant_id, contact_id):
    row = contact(tenant_id, contact_id)
    if not row:
        return
    _require(principal, tenant_id, "emergency.manage" if row["is_emergency"] else "contacts.manage")
    TenantRepo(tenant_id).delete("org_contacts", "id = ?", (contact_id,))
    audit("org_contact.delete", tenant_id, ("org_contact", contact_id), {"name": row["name"]}, actor=principal)


def notices(tenant_id):
    return TenantRepo(tenant_id).all("notices", order="pinned DESC, is_emergency DESC, publish_at DESC")


def notice(tenant_id, notice_id):
    return TenantRepo(tenant_id).one("notices", "id = ?", (notice_id,))


def save_notice(principal, tenant_id, form, notice_id=None):
    _require(principal, tenant_id, "announcements.publish")
    title = " ".join(str(form.get("title") or "").split())[:NOTICE_TITLE_MAX]
    body = str(form.get("body") or "").strip()[:NOTICE_BODY_MAX]
    if not title:
        raise ValueError("Give the notice a title.")
    if not body:
        raise ValueError("Write the notice.")
    expires_at = None
    days = str(form.get("expires_days") or "").strip()
    if days:
        if not days.isdigit() or not 1 <= int(days) <= 365:
            raise ValueError("Show it for 1 to 365 days, or leave it empty to keep it until you delete it.")
        expires_at = int(time.time()) + int(days) * 86400
    values = {
        "title": title, "body": body, "audience": parse_audience(form.get("audience"), tenant_id),
        "pinned": 1 if form.get("pinned") else 0, "is_emergency": 1 if form.get("is_emergency") else 0,
        "expires_at": expires_at,
    }
    repo, now = TenantRepo(tenant_id), int(time.time())
    if notice_id:
        if not repo.update("notices", "id = ?", (notice_id,), updated_at=now, **values):
            raise ValueError("That notice no longer exists.")
        audit("notice.update", tenant_id, ("notice", notice_id), {"title": title}, actor=principal)
        return notice_id
    new_id = repo.insert("notices", publish_at=now, created_by=principal, created_at=now, updated_at=now, **values)
    audit("notice.add", tenant_id, ("notice", new_id), {"title": title, "emergency": bool(values["is_emergency"])}, actor=principal)
    return new_id


def delete_notice(principal, tenant_id, notice_id):
    row = notice(tenant_id, notice_id)
    if not row:
        return
    _require(principal, tenant_id, "announcements.publish")
    TenantRepo(tenant_id).delete("notices", "id = ?", (notice_id,))
    audit("notice.delete", tenant_id, ("notice", notice_id), {"title": row["title"]}, actor=principal)


# ---------------------------------------------------------------- what a member sees (USSD, app)

def visible_notices(tenant_id, phone):
    roles = viewer_roles(tenant_id, phone)
    if roles is None or not tenancy.module_enabled(tenant_id, "announcements"):
        return []
    now = int(time.time())
    rows = TenantRepo(tenant_id).all(
        "notices", "publish_at <= ? AND (expires_at IS NULL OR expires_at > ?)", (now, now),
        order="pinned DESC, is_emergency DESC, publish_at DESC",
    )
    return [r for r in rows if _visible(r, roles)]


def visible_contacts(tenant_id, phone, emergency):
    roles = viewer_roles(tenant_id, phone)
    module = "emergency" if emergency else "directory"
    if roles is None or not tenancy.module_enabled(tenant_id, module):
        return []
    rows = TenantRepo(tenant_id).all("org_contacts", "is_emergency = ?", (1 if emergency else 0,), order="sort, group_name, name")
    return [r for r in rows if _visible(r, roles)]


# ---------------------------------------------------------------- members (portal)

def add_member_with_role(principal, tenant_id, phone, name, role_key):
    """Add someone to the organisation and give them a role (only roles the operator may give)."""
    if not can_manage(principal, tenant_id, "members.manage"):
        raise NotAllowed("You don't have permission to add members.")
    as_platform = rbac.can(principal, "platform.tenants.manage")
    membership_id = tenancy.add_member(principal, tenant_id, phone, name)
    member_phone = TenantRepo(tenant_id).one("memberships", "id = ?", (membership_id,))["user_phone"]
    rbac.grant(principal, tenant_id, rbac.user_principal(member_phone), role_key or "member", as_platform=as_platform)
    return membership_id


def remove_member(principal, tenant_id, membership_id):
    if not can_manage(principal, tenant_id, "members.manage"):
        raise NotAllowed("You don't have permission to remove members.")
    repo = TenantRepo(tenant_id)
    member = repo.one("memberships", "id = ?", (membership_id,))
    if not member:
        return
    member_principal = rbac.user_principal(member["user_phone"])
    if member_principal == principal:
        raise ValueError("You can't remove yourself.")
    as_platform = rbac.can(principal, "platform.tenants.manage")
    for a in rbac.tenant_assignments(tenant_id):
        if a["principal"] == member_principal:
            rbac.revoke(principal, tenant_id, a["id"], as_platform=as_platform)
    repo.update("memberships", "id = ?", (membership_id,), status="inactive", left_at=int(time.time()))
    audit("member.remove", tenant_id, ("member", membership_id), {"phone": member["user_phone"]}, actor=principal)


def member_rows(tenant_id):
    """Active members with their role names, for the portal."""
    roles_by_principal = {}
    for a in rbac.tenant_assignments(tenant_id):
        roles_by_principal.setdefault(a["principal"], []).append(a["name"])
    rows = TenantRepo(tenant_id).all("memberships", "status = 'active'", order="display_name, user_phone")
    return [dict(r, roles=roles_by_principal.get(rbac.user_principal(r["user_phone"]), ["Member"])) for r in rows]
