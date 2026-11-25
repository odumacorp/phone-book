"""Optional in-app notifications about changes an operator or admin makes (they tick "Notify ... in the app").

Members hear about being added, removed, or a change to their role, title or departments; members who can see a
notice or contact hear about it being posted or updated. Each returns how many people got a notification.
"""
from pb.audit import audit
from pb.org import content
from pb.org.reminders import app_notify, recipients
from pb.rbac import engine as rbac
from pb.tenancy import tenants as tenancy
from pb.tenancy.repo import TenantRepo
from pb.text import local_number

SEE_IT = "On USSD choose 3. My organisations, or tap My organisations in the app."


def _role_name(key):
    role = rbac.role_by_key(key)
    return role["name"] if role else key


def _org(tenant_id):
    return tenancy.tenant(tenant_id)["name"]


def _sent(actor, tenant_id, what, count):
    if count:
        audit("notify.app", tenant_id, ("notification", what), {"people": count}, actor=actor)
    return count


def member_added(actor, tenant_id, phone, role_key):
    org = _org(tenant_id)
    count = app_notify([phone], f"{org}: you've been added", f"You're now in {org} as {_role_name(role_key)}. "
                       f"You can see its notices and contacts. {SEE_IT}")
    return _sent(actor, tenant_id, "member_added", count)


def member_removed(actor, tenant_id, phone):
    org = _org(tenant_id)
    count = app_notify([phone], f"{org}: you've been removed",
                       f"You're no longer in {org}, so you won't see its notices and contacts. If this is a mistake, contact {org}.")
    return _sent(actor, tenant_id, "member_removed", count)


def member_changed(actor, tenant_id, phone, before, after):
    """before/after: {"role": key, "title": ..., "placements": [names]}. Only says what changed."""
    org, lines = _org(tenant_id), []
    if before["role"] != after["role"]:
        lines.append(f"Your role is now {_role_name(after['role'])}.")
    if (before["title"] or "") != (after["title"] or ""):
        lines.append(f"Your title is now {after['title']}." if after["title"] else "Your title was removed.")
    if sorted(before["placements"]) != sorted(after["placements"]):
        lines.append("You're now in: " + ", ".join(after["placements"]) + "." if after["placements"]
                     else "You're no longer in any department or group.")
    if not lines:
        return 0
    count = app_notify([phone], f"{org}: your details changed", " ".join(lines))
    return _sent(actor, tenant_id, "member_changed", count)


def notice_posted(actor, tenant_id, notice_id, updated=False):
    row = TenantRepo(tenant_id).one("notices", "id = ?", (notice_id,))
    if not row:
        return 0
    org = _org(tenant_id)
    title = f"{org}: {'updated: ' if updated else ''}{row['title']}"
    count = app_notify(recipients(tenant_id, row["audience"]), title, row["body"])
    return _sent(actor, tenant_id, "notice", count)


def contact_saved(actor, tenant_id, contact_id, updated=False):
    row = TenantRepo(tenant_id).one("org_contacts", "id = ?", (contact_id,))
    if not row:
        return 0
    org = _org(tenant_id)
    kind = "emergency contact" if row["is_emergency"] else "contact"
    what = f"{row['name']}" + (f" ({row['title']})" if row["title"] else "") + f": {local_number(row['phone'])}"
    body = f"{'Updated' if updated else 'New'} {kind} in {org}: {what}. {SEE_IT}"
    count = app_notify(recipients(tenant_id, row["audience"]), f"{org}: {'updated' if updated else 'new'} {kind}", body)
    return _sent(actor, tenant_id, "contact", count)


def contacts_added(actor, tenant_id, contact_ids):
    """One notification for several new contacts (added from members), to the people who can see them."""
    repo = TenantRepo(tenant_id)
    rows = [r for r in (repo.one("org_contacts", "id = ?", (cid,)) for cid in contact_ids) if r]
    if not rows:
        return 0
    if len(rows) == 1:
        return contact_saved(actor, tenant_id, rows[0]["id"])
    org = _org(tenant_id)
    names = ", ".join(r["name"] for r in rows[:5]) + (f" and {len(rows) - 5} more" if len(rows) > 5 else "")
    body = f"{len(rows)} new contacts in {org}: {names}. {SEE_IT}"
    count = app_notify(recipients(tenant_id, rows[0]["audience"]), f"{org}: {len(rows)} new contacts", body)
    return _sent(actor, tenant_id, "contacts", count)


def member_snapshot(tenant_id, membership_id):
    """Role, title and department/group names of a member, to compare before and after an edit."""
    from pb.org import structure
    member = TenantRepo(tenant_id).one("memberships", "id = ?", (membership_id,))
    names = structure.placements_by_member(tenant_id).get(membership_id, [])
    return {"role": content.member_role(tenant_id, member["user_phone"])[0], "title": member["title"], "placements": names,
            "phone": member["user_phone"]}
