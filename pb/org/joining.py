"""Joining an organisation at sign-up (or later from the app): pick it from the list or type its org code.

Each organisation chooses (Settings, "join_mode"):
- "open": anyone who asks becomes a Member straight away;
- "approval" (default): a join request waits for the Super Admin or an Admin to approve or decline it;
- "closed": not listed; people can still ask with the org code, and someone must approve them.
Someone the organisation already added by phone number is simply a member (nothing to approve).
"""
import time

from pb.audit import audit
from pb.db import fetch_all
from pb.org import content
from pb.org.reminders import app_notify
from pb.rbac import engine as rbac
from pb.tenancy import tenants as tenancy
from pb.tenancy.repo import TenantRepo
from pb.text import local_number
from pb.users import get_user

JOINABLE_STATUSES = ("trial", "active")


def public_organisations():
    """Organisations people can choose at sign-up (open or by approval; invite-only ones aren't listed)."""
    rows = fetch_all("SELECT id, name, edition FROM tenants WHERE status IN ('trial', 'active') ORDER BY name")
    names = {e["key"]: e["name"] for e in tenancy.editions()}
    return [{"id": r["id"], "name": r["name"], "kind": names.get(r["edition"], "")} for r in rows
            if tenancy.get_setting(r["id"], "join_mode") != "closed"]


def find(org_id=None, org_code=None):
    """The organisation chosen at sign-up: from the list (id) or by its org code. None if it can't be joined."""
    tenant = None
    if org_code:
        code = "".join(ch for ch in str(org_code) if ch.isdigit())
        row = fetch_all("SELECT id FROM tenants WHERE org_code = ?", (code,)) if code else []
        tenant = tenancy.tenant(row[0]["id"]) if row else None
    elif str(org_id or "").isdigit():
        tenant = tenancy.tenant(int(org_id))
        if tenant and tenancy.get_setting(tenant["id"], "join_mode") == "closed":
            tenant = None  # invite-only organisations aren't listed; they need the code
    if not tenant or tenant["status"] not in JOINABLE_STATUSES:
        return None
    return tenant


def request_join(tenant_id, phone, name=None):
    """Returns ("member" | "pending" | "already", organisation name)."""
    t = tenancy.tenant(tenant_id)
    repo = TenantRepo(tenant_id)
    member = repo.one("memberships", "user_phone = ?", (phone,))
    if member and member["status"] == "active":
        return "already", t["name"]
    if member and member["status"] == "pending":
        return "pending", t["name"]
    mode = tenancy.get_setting(tenant_id, "join_mode")
    now = int(time.time())
    if mode == "open":
        if member:
            repo.update("memberships", "id = ?", (member["id"],), status="active", left_at=None, joined_at=now, joined_via="signup")
        else:
            repo.insert("memberships", user_phone=phone, display_name=name, status="active", joined_at=now, joined_via="signup")
        rbac.grant_system(tenant_id, rbac.user_principal(phone), "member", "joined at sign-up")
        audit("member.join", tenant_id, ("phone", phone), {"mode": "open"}, actor=rbac.user_principal(phone))
        return "member", t["name"]
    if member:
        repo.update("memberships", "id = ?", (member["id"],), status="pending", joined_at=now, joined_via="signup")
    else:
        repo.insert("memberships", user_phone=phone, display_name=name, status="pending", joined_at=now, joined_via="signup")
    audit("member.join_request", tenant_id, ("phone", phone), {"mode": mode}, actor=rbac.user_principal(phone))
    _tell_managers(tenant_id, t["name"], phone, name)
    return "pending", t["name"]


def _tell_managers(tenant_id, org_name, phone, name):
    managers = [a["principal"][5:] for a in rbac.tenant_assignments(tenant_id)
                if a["key"] in ("org_owner", "org_admin") and a["principal"].startswith("user:")]
    who = f"{name} ({local_number(phone)})" if name else local_number(phone)
    app_notify(managers, f"{org_name}: request to join",
               f"{who} asked to join {org_name}. Approve or decline it in the Platform: Members → Join requests.")


def requests(tenant_id):
    return TenantRepo(tenant_id).all("memberships", "status = 'pending'", order="joined_at")


def approve(principal, tenant_id, membership_id, role_key="member", notify=True):
    if not content.can_manage(principal, tenant_id, "members.manage"):
        raise content.NotAllowed("You don't have permission to approve members.")
    repo = TenantRepo(tenant_id)
    member = repo.one("memberships", "id = ? AND status = 'pending'", (membership_id,))
    if not member:
        raise ValueError("That request has already been answered.")
    if role_key == tenancy.SUPER_ADMIN:
        raise ValueError("Approve them first; then hand over the Super Admin role with Edit.")
    if not tenancy.role_offered(tenant_id, role_key or "member"):
        raise ValueError("That role isn't used in this organisation.")
    principal_new = rbac.user_principal(member["user_phone"])
    if role_key == tenancy.ADMIN:
        tenancy.check_admin_room(tenant_id, principal_new)
    rbac.grant(principal, tenant_id, principal_new, role_key or "member", as_platform=rbac.can(principal, "platform.tenants.manage"))
    repo.update("memberships", "id = ?", (membership_id,), status="active", joined_at=int(time.time()), left_at=None)
    audit("member.approve", tenant_id, ("member", membership_id), {"phone": member["user_phone"], "role": role_key}, actor=principal)
    if notify:
        from pb.org import change_notices
        change_notices.member_added(principal, tenant_id, member["user_phone"], role_key or "member")


def decline(principal, tenant_id, membership_id, notify=True):
    if not content.can_manage(principal, tenant_id, "members.manage"):
        raise content.NotAllowed("You don't have permission to decline requests.")
    repo = TenantRepo(tenant_id)
    member = repo.one("memberships", "id = ? AND status = 'pending'", (membership_id,))
    if not member:
        raise ValueError("That request has already been answered.")
    repo.update("memberships", "id = ?", (membership_id,), status="declined", left_at=int(time.time()))
    audit("member.decline", tenant_id, ("member", membership_id), {"phone": member["user_phone"]}, actor=principal)
    if notify and get_user(member["user_phone"]):
        org = tenancy.tenant(tenant_id)["name"]
        app_notify([member["user_phone"]], f"{org}: request not approved",
                   f"Your request to join {org} wasn't approved. If you think this is a mistake, contact {org}.")
