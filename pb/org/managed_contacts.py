"""Who keeps a member's personal phone book: the member, or their organisation.

Each organisation chooses (Settings, "member_contacts"):
- "own" (default): members add, edit, delete and sync their own contacts, as everyone does;
- "managed": the organisation provides the numbers. Its members (not its admins and operators, who hold
  contacts.manage there) can't add, edit, delete or sync personal contacts on USSD, in the app or on the website.
  Operators choose which organisation contacts show in members' lists (pb.org.provided).
"""
from pb.audit import audit
from pb.org import content
from pb.rbac import engine as rbac
from pb.tenancy import tenants as tenancy
from pb.tenancy.repo import TenantRepo

MANAGED_MESSAGE = "Your contacts are provided by {org}. Ask {org} to add the numbers you need."


def managed_by(phone):
    """The organisation (dict with id, name) that manages this person's contacts, or None if they manage their own."""
    for o in content.my_organisations(phone):
        if tenancy.get_setting(o["tenant_id"], "member_contacts") != "managed":
            continue
        if rbac.can(rbac.user_principal(phone), "contacts.manage", o["tenant_id"]):
            continue  # its admins and operators keep their own phone book
        return {"id": o["tenant_id"], "name": o["name"]}
    return None


def blocked_message(phone):
    org = managed_by(phone)
    return MANAGED_MESSAGE.format(org=org["name"]) if org else None


def show_in_phone_books(principal, tenant_id, emergency=None, show=True):
    """Tick (or untick) "Show in members' own contact lists" on all the organisation's contacts (or one list of them).
    Returns how many changed."""
    if not content.can_manage(principal, tenant_id, "emergency.manage" if emergency else "contacts.manage"):
        raise content.NotAllowed("You don't have permission to change that.")
    rows = [c for c in content.contacts(tenant_id, emergency=emergency) if bool(c["in_phone_books"]) != show]
    repo = TenantRepo(tenant_id)
    for c in rows:
        repo.update("org_contacts", "id = ?", (c["id"],), in_phone_books=1 if show else 0)
    audit("org_contact.phone_books", tenant_id, ("org_contacts", "emergency" if emergency else "contacts"),
          {"show": show, "changed": len(rows)}, actor=principal)
    return len(rows)


def set_one(principal, tenant_id, contact_id, show):
    row = content.contact(tenant_id, contact_id)
    if not row:
        raise ValueError("That contact no longer exists.")
    if not content.can_manage(principal, tenant_id, "emergency.manage" if row["is_emergency"] else "contacts.manage"):
        raise content.NotAllowed("You don't have permission to change that.")
    TenantRepo(tenant_id).update("org_contacts", "id = ?", (contact_id,), in_phone_books=1 if show else 0)
    audit("org_contact.phone_books", tenant_id, ("org_contact", contact_id), {"show": show, "name": row["name"]}, actor=principal)
    return row
