"""Organisation contacts shown automatically in members' own contact lists.

The admin ticks "Show in members' own contact lists" on a contact. Every member allowed to see it then finds it in
their own list (app, website, USSD "1. My contacts", search and "SMS me my contacts"), always as the organisation
last saved it: edits show at once; unticking or deleting removes it. Members can't change or delete these.
They're listed with negative ids (-org contact id), so they never clash with the member's own contacts.
"""
from pb.org import content
from pb.tenancy.repo import TenantRepo


def provided_contacts(owner):
    """Contacts this person's organisations put in their list, as rows like their own contacts (plus "org")."""
    rows, seen = [], set()
    for org in content.my_organisations(owner):
        tags = content.viewer_roles(org["tenant_id"], owner) or set()
        member = TenantRepo(org["tenant_id"]).one("memberships", "user_phone = ? AND status = 'active'", (owner,))
        picked = content.selected_only(member)  # None: the defaults; else only the contacts picked for them
        for emergency in (True, False):
            for c in content.visible_contacts(org["tenant_id"], owner, emergency=emergency):
                if c["phone"] in seen or c["phone"] == owner:
                    continue
                if picked is not None:
                    if c["id"] not in picked:
                        continue
                elif not c["in_phone_books"] or not content.matches(c["phone_book_audience"], tags):
                    continue  # not in this person's list (roles, departments, groups, or changes for them)
                seen.add(c["phone"])
                rows.append({"id": -c["id"], "name": c["name"], "phone": c["phone"], "priority": 0, "enc": 0,
                             "org": org["name"], "org_id": org["tenant_id"], "title": c["title"]})
    return rows


def with_provided(owner, own_rows):
    """The person's own contacts plus their organisations' (their own copy wins if they saved the same number)."""
    own_phones = {r["phone"] for r in own_rows}
    extra = [r for r in provided_contacts(owner) if r["phone"] not in own_phones]
    merged = [dict(r) for r in own_rows] + extra
    return sorted(merged, key=lambda r: (-r["priority"], r["name"].lower(), r["id"]))
