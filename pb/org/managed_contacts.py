"""Who keeps a member's personal phone book: the member, or their organisation.

Each organisation chooses (Settings, "member_contacts"):
- "own" (default): members add, edit, delete and sync their own contacts, as everyone does;
- "managed": the organisation provides the numbers. Its members (not its admins and operators, who hold
  contacts.manage there) can't add, edit, delete or sync personal contacts on USSD, in the app or on the website.
  Operators put contacts into members' phone books from the Contacts page ("Put in members' phone books").
"""
from pb.audit import audit
from pb.contacts import stored_contact
from pb.db import get_db, sql
from pb.org import content
from pb.org.reminders import recipients
from pb.rbac import engine as rbac
from pb.tenancy import tenants as tenancy

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


def push_to_phone_books(principal, tenant_id, emergency=None):
    """Copy the organisation's contacts into the phone books of the members allowed to see each one.
    Numbers a member already has are left alone. Returns (people reached, contacts added)."""
    if not content.can_manage(principal, tenant_id, "contacts.manage"):
        raise content.NotAllowed("You don't have permission to add contacts to members' phone books.")
    rows = content.contacts(tenant_id, emergency=emergency)
    if not rows:
        raise ValueError("Add some contacts first.")
    db = get_db()
    existing = {}
    added, reached = 0, set()
    for c in rows:
        for phone in recipients(tenant_id, c["audience"]):
            if phone == c["phone"]:
                continue  # don't add people to their own phone book
            if phone not in existing:
                existing[phone] = {r["phone"] for r in content_phones(phone)}
            if c["phone"] in existing[phone]:
                continue
            name, number, enc = stored_contact(c["name"], c["phone"])
            db.execute(sql("INSERT INTO contacts (owner, name, phone, enc, source_tenant) VALUES (?, ?, ?, ?, ?)"),
                       (phone, name, number, enc, tenant_id))
            existing[phone].add(c["phone"])
            added += 1
            reached.add(phone)
    db.commit()
    audit("org_contact.push_to_phone_books", tenant_id, ("org_contacts", "all"),
          {"people": len(reached), "contacts_added": added}, actor=principal)
    return len(reached), added


def content_phones(owner):
    from pb.contacts import list_contacts
    return list_contacts(owner)
