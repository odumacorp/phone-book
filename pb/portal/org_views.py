"""The organisation workspace (/platform/org): admins and operators publish what their members see.

Notices, contacts, emergency contacts and members. Each action is permission-checked in pb.org.content, so a
supervisor or publisher only gets the parts their role allows. Platform staff who manage organisations can open
any workspace (from the organisation's page) to help set it up; everything is in the audit log.
"""
import datetime
import time

from flask import abort, g, redirect, render_template, request, url_for

from pb.billing import ussd_codes
from flask import jsonify

from pb.org import change_notices, contact_groups, content, joining, managed_contacts, reminders, structure, suggestions
from pb.tenancy.repo import TenantRepo
from pb.text import local_number
from pb.ussd import orgs as ussd_orgs
from pb.portal import bp, done, failed, is_staff
from pb.rbac import engine as rbac
from pb.tenancy import tenants as tenancy

AREAS = {  # area: (permission, module)
    "notices": ("announcements.publish", "announcements"),
    "contacts": ("contacts.manage", "directory"),
    "emergency": ("emergency.manage", "emergency"),
    "members": ("members.manage", "members"),
    "structure": ("departments.manage", "members"),
    "reminders": ("announcements.publish", "announcements"),
    "preview": (content.MANAGE_PERMISSIONS, None),
    "history": (("announcements.publish", "contacts.manage", "emergency.manage"), None),
    "lists": (("roles.custom", "tenant.admins.manage"), None),
    "settings": ("tenant.settings.manage", None),
}


def workspaces():
    """Organisations the current person can manage something in."""
    if is_staff():
        return []
    rows = []
    for org in tenancy.memberships_of(g.staff["phone"]):
        areas = [a for a, (perm, _m) in AREAS.items() if _holds(perm, org["tenant_id"])]
        if areas:
            rows.append(dict(org, areas=areas))
    return rows


def _holds(perm, tenant_id):
    perms = perm if isinstance(perm, tuple) else (perm,)
    return any(rbac.can(g.principal, p, tenant_id) for p in perms)


def open_workspace(tenant_id):
    """The tenant and the areas this person can use there (404 if none)."""
    t = tenancy.tenant(tenant_id)
    if not t:
        abort(404)
    staff_help = rbac.can(g.principal, "platform.tenants.manage")
    areas = [a for a, (perm, module) in AREAS.items()
             if (staff_help or _holds(perm, tenant_id)) and (module is None or tenancy.module_enabled(tenant_id, module))]
    if not areas:
        abort(404)
    g.org, g.org_areas, g.org_staff_help = t, areas, staff_help
    return t, areas


def page(template, **values):
    tenant_id = g.org["id"]
    return render_template(template, org=g.org, areas=g.org_areas, staff_help=g.org_staff_help, page="org",
                           audience_departments=[(d["id"], d["name"]) for d in structure.departments(tenant_id)],
                           audience_groups=[(x["id"], x["name"]) for x in structure.groups(tenant_id)], **values)


def audience_choices(tenant_id):
    """Roles to choose from here: the ones for this kind of organisation, then the organisation's own."""
    return tenancy.available_roles(tenant_id)


def title_lists_for(tenant_id, used=()):
    """Job-title choices: the organisation's own list, titles already in use, then suggestions for its kind."""
    own = tenancy.org_titles(tenant_id)
    seen = {t.lower() for t in own}
    used = [t for t in sorted({u for u in used if u}, key=str.lower) if t.lower() not in seen]
    seen |= {t.lower() for t in used}
    suggested = [t for t in suggestions.title_options(g.org["edition"]) if t.lower() not in seen]
    return [("Your titles", own), ("Used in your organisation", used), ("Suggested for you", suggested)]


def audience_text(stored, choices):
    keys = content.audience_roles(stored)
    if not keys:
        return "Everyone"
    names = dict(choices)
    tenant_id = g.org["id"]
    names.update({f"d:{d['id']}": d["name"] for d in structure.departments(tenant_id)})
    names.update({f"g:{x['id']}": x["name"] for x in structure.groups(tenant_id)})
    for m in TenantRepo(tenant_id).all("memberships"):
        names[f"m:{m['id']}"] = m["display_name"] or local_number(m["user_phone"])
    people = [names.get(k, "a removed group") for k in keys if not k.startswith("!")]
    left_out = [names.get(k[1:], "someone") for k in keys if k.startswith("!")]
    text = ", ".join(people) if people else "Everyone"
    return text + (f" (not {', '.join(left_out)})" if left_out else "")


def require_area(area):
    if area not in g.org_areas:
        abort(403)


# ---------------------------------------------------------------- home

@bp.route("/org", methods=["GET"])
def org_home():
    rows = workspaces()
    if len(rows) == 1:
        return redirect(url_for("platform.org_overview", tenant_id=rows[0]["tenant_id"]))
    return render_template("platform/org/pick.html", page="org", rows=rows)


@bp.route("/org/<int:tenant_id>", methods=["GET"])
def org_overview(tenant_id):
    t, areas = open_workspace(tenant_id)
    allocations = ussd_codes.allocations(tenant_id)
    counts = {
        "notices": len(content.notices(tenant_id)),
        "contacts": len(content.contacts(tenant_id, emergency=False)),
        "emergency": len(content.contacts(tenant_id, emergency=True)),
        "members": len(content.member_rows(tenant_id)),
    }
    preview = [label for key, label in (("notices", "Notices"), ("contacts", "Contacts"), ("emergency", "Emergency contacts"))
               if tenancy.module_enabled(tenant_id, AREAS[key][1])]
    return page("platform/org/overview.html", counts=counts, preview=preview, main_code=ussd_codes.main_code(),
                dial=ussd_codes.dial_string(allocations[0]) if allocations else None)


# ---------------------------------------------------------------- notices

@bp.route("/org/<int:tenant_id>/notices", methods=["GET"])
def org_notices(tenant_id):
    open_workspace(tenant_id)
    require_area("notices")
    choices = audience_choices(tenant_id)
    rows = [dict(n, audience_text=audience_text(n["audience"], choices)) for n in content.notices(tenant_id)]
    editing = content.notice(tenant_id, int(request.args["edit"])) if request.args.get("edit", "").isdigit() else None
    today = datetime.datetime.now(content.EAT).date()
    expiry_date = (datetime.datetime.fromtimestamp(editing["expires_at"], content.EAT).date().isoformat()
                   if editing and editing["expires_at"] else "")
    return page("platform/org/notices.html", rows=rows, choices=choices, editing=editing, now=int(time.time()),
                expiry_choices=content.EXPIRY_CHOICES, expiry_date=expiry_date, today=today.isoformat(),
                max_date=(today + datetime.timedelta(days=content.MAX_EXPIRY_DAYS)).isoformat(),
                editing_audience=content.audience_roles(editing["audience"]) if editing else [])


@bp.route("/org/<int:tenant_id>/notices", methods=["POST"])
def org_notice_save(tenant_id):
    open_workspace(tenant_id)
    require_area("notices")
    form = {**request.form.to_dict(), "audience": request.form.getlist("audience")}
    notice_id = int(request.form["notice_id"]) if request.form.get("notice_id", "").isdigit() else None
    try:
        saved_id = content.save_notice(g.principal, tenant_id, form, notice_id)
    except (ValueError, content.NotAllowed) as e:
        return failed(str(e), "platform.org_notices", tenant_id=tenant_id, **({"edit": notice_id} if notice_id else {}))
    message = "Notice saved. Members see it on USSD and in the app now." if not notice_id else "Notice updated."
    if request.form.get("notify"):
        message += _told(change_notices.notice_posted(g.principal, tenant_id, saved_id, updated=bool(notice_id)))
    return done(message, "platform.org_notices", tenant_id=tenant_id)


def _told(count):
    return f" {count} {'person was' if count == 1 else 'people were'} notified in the app." if count else \
        " Nobody with the app could be notified."


@bp.route("/org/<int:tenant_id>/notices/<int:notice_id>/delete", methods=["POST"])
def org_notice_delete(tenant_id, notice_id):
    open_workspace(tenant_id)
    require_area("notices")
    try:
        content.delete_notice(g.principal, tenant_id, notice_id)
    except content.NotAllowed as e:
        return failed(str(e), "platform.org_notices", tenant_id=tenant_id)
    return done("Notice deleted.", "platform.org_notices", tenant_id=tenant_id)


# ---------------------------------------------------------------- contacts and emergency contacts

def _contacts_page(tenant_id, emergency):
    open_workspace(tenant_id)
    require_area("emergency" if emergency else "contacts")
    choices = audience_choices(tenant_id)
    rows = [dict(c, audience_text=audience_text(c["audience"], choices),
                 pb_text=audience_text(c["phone_book_audience"], choices) if c["phone_book_audience"] else "Everyone who can see it")
            for c in content.contacts(tenant_id, emergency=emergency)]
    editing = content.contact(tenant_id, int(request.args["edit"])) if request.args.get("edit", "").isdigit() else None
    if editing and bool(editing["is_emergency"]) != emergency:
        editing = None
    edition = g.org["edition"]
    defined = structure.names(tenant_id)
    used_groups = sorted({c["group_name"] for c in rows if c["group_name"]} - set(defined), key=str.lower)
    every_group = set(defined) | set(used_groups)
    group_lists = [("Your departments and groups", defined), ("Used on contacts", used_groups),
                   ("Suggested for you", [x for x in suggestions.group_options(edition, []) if x not in every_group])]
    title_lists = title_lists_for(tenant_id, [c["title"] for c in content.contacts(tenant_id)])
    name_lists = [("Suggested", suggestions.emergency_options(edition, [c["name"] for c in rows]))] if emergency else []
    return page("platform/org/contacts.html", rows=rows, choices=choices, editing=editing, emergency=emergency,
                editing_pb=[e for e in content.audience_roles(editing["phone_book_audience"]) if not e.lstrip("!").startswith("m:")] if editing else [],
                editing_pb_personal=bool(editing) and any(e.lstrip("!").startswith("m:") for e in content.audience_roles(editing["phone_book_audience"])),
                group_lists=group_lists, title_lists=title_lists, name_lists=name_lists,
                member_options=content.members_for_contacts(tenant_id, emergency),
                member_contacts=tenancy.get_setting(tenant_id, "member_contacts"),
                can_structure="structure" in g.org_areas,
                editing_audience=content.audience_roles(editing["audience"]) if editing else [],
                endpoint="platform.org_emergency" if emergency else "platform.org_contacts")


def _contact_save(tenant_id, emergency):
    open_workspace(tenant_id)
    require_area("emergency" if emergency else "contacts")
    endpoint = "platform.org_emergency" if emergency else "platform.org_contacts"
    form = {**request.form.to_dict(), "audience": request.form.getlist("audience"), "pb_audience": request.form.getlist("pb_audience")}
    contact_id = int(request.form["contact_id"]) if request.form.get("contact_id", "").isdigit() else None
    try:
        saved_id = content.save_contact(g.principal, tenant_id, form, emergency, contact_id)
    except (ValueError, content.NotAllowed) as e:
        return failed(str(e), endpoint, tenant_id=tenant_id, **({"edit": contact_id} if contact_id else {}))
    message = "Contact updated." if contact_id else "Contact added. Members can find it on USSD and in the app now."
    if request.form.get("notify"):
        message += _told(change_notices.contact_saved(g.principal, tenant_id, saved_id, updated=bool(contact_id)))
    return done(message, endpoint, tenant_id=tenant_id)


@bp.route("/org/<int:tenant_id>/contacts", methods=["GET"])
def org_contacts(tenant_id):
    return _contacts_page(tenant_id, emergency=False)


@bp.route("/org/<int:tenant_id>/contacts", methods=["POST"])
def org_contact_save(tenant_id):
    return _contact_save(tenant_id, emergency=False)


@bp.route("/org/<int:tenant_id>/emergency", methods=["GET"])
def org_emergency(tenant_id):
    return _contacts_page(tenant_id, emergency=True)


@bp.route("/org/<int:tenant_id>/emergency", methods=["POST"])
def org_emergency_save(tenant_id):
    return _contact_save(tenant_id, emergency=True)


@bp.route("/org/<int:tenant_id>/<any(contacts, emergency):kind>/from-members", methods=["POST"])
def org_contacts_from_members(tenant_id, kind):
    emergency = kind == "emergency"
    open_workspace(tenant_id)
    require_area("emergency" if emergency else "contacts")
    endpoint = "platform.org_emergency" if emergency else "platform.org_contacts"
    f = request.form
    group = None if f.get("group_mode", "own") == "own" else (" ".join((f.get("group_name") or "").split())[:40])
    try:
        audience = content.parse_audience(f.getlist("audience"), tenant_id)
        added, skipped = content.add_contacts_from_members(g.principal, tenant_id, f.getlist("members"), emergency, group, audience,
                                                           in_phone_books=bool(f.get("in_phone_books")))
    except (ValueError, content.NotAllowed) as e:
        return failed(str(e), endpoint, tenant_id=tenant_id)
    message = f"Added {len(added)} contact{'' if len(added) == 1 else 's'} from your members."
    if skipped:
        message += f" {skipped} already on the list."
    if f.get("notify"):
        message += _told(change_notices.contacts_added(g.principal, tenant_id, added))
    return done(message, endpoint, tenant_id=tenant_id)


@bp.route("/org/<int:tenant_id>/<any(contacts, emergency):kind>/phone-books", methods=["POST"])
def org_contacts_phone_books(tenant_id, kind):
    """Show (or stop showing) all of a list's contacts in members' own contact lists."""
    emergency = kind == "emergency"
    open_workspace(tenant_id)
    require_area("emergency" if emergency else "contacts")
    endpoint = "platform.org_emergency" if emergency else "platform.org_contacts"
    show = request.form.get("show") == "1"
    try:
        changed = managed_contacts.show_in_phone_books(g.principal, tenant_id, emergency=emergency, show=show)
    except (ValueError, content.NotAllowed) as e:
        return failed(str(e), endpoint, tenant_id=tenant_id)
    message = (f"{changed} contact{'' if changed == 1 else 's'} now show{'s' if changed == 1 else ''} in members' own contact lists."
               if show else f"{changed} contact{'' if changed == 1 else 's'} no longer show{'s' if changed == 1 else ''} in members' lists.")
    if show and changed and request.form.get("notify"):
        org = g.org["name"]
        count = reminders.app_notify(reminders.recipients(tenant_id, ""), f"{org}: new numbers in your contacts",
                                     f"{org} added contacts to your Remote Phonebook list. Open the app, or dial and choose 1. My contacts.")
        message += _told(count)
    return done(message, endpoint, tenant_id=tenant_id)


@bp.route("/org/<int:tenant_id>/contacts/<int:contact_id>/phone-books", methods=["POST"])
def org_contact_phone_books(tenant_id, contact_id):
    open_workspace(tenant_id)
    show = request.form.get("show") == "1"
    try:
        row = managed_contacts.set_one(g.principal, tenant_id, contact_id, show)
    except (ValueError, content.NotAllowed) as e:
        return failed(str(e), "platform.org_contacts", tenant_id=tenant_id)
    endpoint = "platform.org_emergency" if row["is_emergency"] else "platform.org_contacts"
    return done(f"{row['name']} {'now shows' if show else 'no longer shows'} in members' own contact lists.", endpoint, tenant_id=tenant_id)


@bp.route("/org/<int:tenant_id>/contacts/<int:contact_id>/delete", methods=["POST"])
def org_contact_delete(tenant_id, contact_id):
    open_workspace(tenant_id)
    row = content.contact(tenant_id, contact_id)
    endpoint = "platform.org_emergency" if row and row["is_emergency"] else "platform.org_contacts"
    try:
        content.delete_contact(g.principal, tenant_id, contact_id)
    except content.NotAllowed as e:
        return failed(str(e), endpoint, tenant_id=tenant_id)
    return done("Contact deleted.", endpoint, tenant_id=tenant_id)


# ---------------------------------------------------------------- members

MEMBER_TABS = [  # (key, label) for the Members page tabs
    ("all", "All"), ("admins", "Admins"), ("staff", "Staff"), ("members", "Members"), ("clients", "Clients"),
    ("guardians", "Guardians"), ("requests", "Join requests"), ("removed", "Removed"),
]


def member_category(role_key):
    """Which tab a member's main role puts them in."""
    return {"org_owner": "admins", "org_admin": "admins", "member": "members", "client": "clients",
            "guardian": "guardians"}.get(role_key, "staff")


@bp.route("/org/<int:tenant_id>/members", methods=["GET"])
def org_members(tenant_id):
    open_workspace(tenant_id)
    require_area("members")
    choices = [(k, n) for k, n in audience_choices(tenant_id)]
    placements = structure.placements_by_member(tenant_id)
    members = content.member_rows(tenant_id)
    seen = content.last_logins(m["user_phone"] for m in members)
    rows = [dict(m, placed=placements.get(m["id"], []), last_seen=seen.get(m["user_phone"]),
                 category=member_category(m["role_key"])) for m in members]
    editing = next((m for m in rows if str(m["id"]) == request.args.get("edit")), None)
    editing_scope = editing_placements = None
    list_contacts_for = []
    if editing:
        tags = content.member_tags(tenant_id).get(editing["user_phone"], set())
        names = dict(audience_choices(tenant_id))
        names.update({f"d:{d['id']}": d["name"] for d in structure.departments(tenant_id)})
        names.update({f"g:{x['id']}": x["name"] for x in structure.groups(tenant_id)})
        only = editing["list_mode"] == "selected"
        list_contacts_for = [dict(c, reason=("picked for them" if c["shown"] else "not picked") if only
                                  else ("from contact group " + ", ".join(c["via_groups"])
                                        if c["shown"] and c["via_groups"] and not (c["in_phone_books"] and content.matches(c["phone_book_audience"], tags))
                                        else content.list_reason(c, tags, editing["id"], names)))
                             for c in content.member_list_contacts(tenant_id, editing["id"])]
        _key, editing_scope = content.member_role(tenant_id, editing["user_phone"])
        editing_placements = structure.placements_of(tenant_id, editing["id"])
    title_lists = title_lists_for(tenant_id, [m["title"] for m in rows])
    return page("platform/org/members.html", rows=rows, choices=choices,
                departments=structure.departments(tenant_id), groups=structure.groups(tenant_id),
                can_structure="structure" in g.org_areas, editing=editing, editing_scope=editing_scope,
                editing_placements=editing_placements or [], title_lists=title_lists, list_contacts_for=list_contacts_for,
                can_contacts="contacts" in g.org_areas or "emergency" in g.org_areas,
                former=content.former_members(tenant_id), me=g.principal,
                super_admin=tenancy.super_admin(tenant_id),
                can_hand_over=g.org_staff_help or tenancy.super_admin(tenant_id) == g.principal,
                admin_limit=tenancy.admin_limit(tenant_id), admin_count=len(tenancy.admins(tenant_id)),
                join_requests=joining.requests(tenant_id), join_mode=tenancy.get_setting(tenant_id, "join_mode"),
                tabs=MEMBER_TABS, tab=request.args.get("tab") or ("requests" if joining.requests(tenant_id) and not request.args.get("edit") else "all"))


@bp.route("/org/<int:tenant_id>/members", methods=["POST"])
def org_member_add(tenant_id):
    open_workspace(tenant_id)
    require_area("members")
    try:
        membership_id = content.add_member_with_role(g.principal, tenant_id, request.form.get("phone"), request.form.get("name"),
                                                     request.form.get("role") or "member", request.form.get("title"))
        if request.form.get("placement"):
            structure.place_member(g.principal, tenant_id, membership_id, request.form.get("placement"))
    except (ValueError, content.NotAllowed, rbac.PermissionDenied) as e:
        return failed(str(e), "platform.org_members", tenant_id=tenant_id)
    message = "Member added. They see your notices and contacts once they log in with that number."
    if request.form.get("notify"):
        snap = change_notices.member_snapshot(tenant_id, membership_id)
        if change_notices.member_added(g.principal, tenant_id, snap["phone"], snap["role"]):
            message += " They were notified in the app."
        else:
            message += " They don't have the app yet, so they weren't notified."
    return done(message, "platform.org_members", tenant_id=tenant_id)


@bp.route("/org/<int:tenant_id>/members/<int:membership_id>", methods=["POST"])
def org_member_update(tenant_id, membership_id):
    open_workspace(tenant_id)
    require_area("members")
    form = {**request.form.to_dict(), "placements": request.form.getlist("placements")}
    before = change_notices.member_snapshot(tenant_id, membership_id) if request.form.get("notify") else None
    try:
        content.update_member(g.principal, tenant_id, membership_id, form)
    except (ValueError, content.NotAllowed, rbac.PermissionDenied) as e:
        message = str(e)
        if isinstance(e, rbac.PermissionDenied):
            message = "You can't give that role: it has permissions you don't have" + (
                f" ({message.split(': ', 1)[1]})." if ": " in message else ".")
        return failed(message, "platform.org_members", tenant_id=tenant_id, edit=membership_id)
    message = "Member updated."
    if before:
        after = change_notices.member_snapshot(tenant_id, membership_id)
        if change_notices.member_changed(g.principal, tenant_id, after["phone"], before, after):
            message += " They were notified in the app."
    return done(message, "platform.org_members", tenant_id=tenant_id)


@bp.route("/org/<int:tenant_id>/members/<int:membership_id>/contacts", methods=["POST"])
def org_member_list_contacts(tenant_id, membership_id):
    """Which organisation contacts show in this one member's own contact list."""
    open_workspace(tenant_id)
    require_area("members")
    try:
        changed = content.set_member_list_contacts(g.principal, tenant_id, membership_id, request.form.getlist("contacts"))
    except (ValueError, content.NotAllowed) as e:
        return failed(str(e), "platform.org_members", tenant_id=tenant_id, edit=membership_id)
    return done("Their contact list is updated." if changed else "Nothing changed.", "platform.org_members",
                tenant_id=tenant_id, edit=membership_id, _anchor="their-contacts")


@bp.route("/org/<int:tenant_id>/members/<int:membership_id>/contacts/<any(mode, clear, reset):action>", methods=["POST"])
def org_member_list_action(tenant_id, membership_id, action):
    """A member's own contact list: how it works (defaults / only picked), clear it, or reset it to the defaults."""
    open_workspace(tenant_id)
    require_area("members")
    try:
        if action == "mode":
            content.set_member_list_mode(g.principal, tenant_id, membership_id, request.form.get("mode"))
            message = ("Their list now shows only the contacts you pick for them." if request.form.get("mode") == "selected"
                       else "Their list follows the default lists again (plus any changes for them).")
        elif action == "clear":
            content.set_member_list_contacts(g.principal, tenant_id, membership_id, [])
            message = "Their contact list is cleared: no organisation contacts in it."
        else:
            content.reset_member_list(g.principal, tenant_id, membership_id)
            message = "Their list follows the default lists again; changes made just for them are undone."
    except (ValueError, content.NotAllowed) as e:
        return failed(str(e), "platform.org_members", tenant_id=tenant_id, edit=membership_id, _anchor="their-contacts")
    return done(message, "platform.org_members", tenant_id=tenant_id, edit=membership_id, _anchor="their-contacts")


@bp.route("/org/<int:tenant_id>/members/<int:membership_id>/contacts/new", methods=["POST"])
def org_member_new_contact(tenant_id, membership_id):
    """Add a new organisation contact and put it in this one member's list."""
    open_workspace(tenant_id)
    require_area("members")
    emergency = request.form.get("emergency") == "1"
    form = {"name": request.form.get("name"), "phone": request.form.get("phone"), "title": request.form.get("title"),
            "audience": [], "in_phone_books": "1", "pb_audience": [f"m:{membership_id}"]}
    try:
        content.save_contact(g.principal, tenant_id, form, emergency)
    except (ValueError, content.NotAllowed) as e:
        return failed(str(e), "platform.org_members", tenant_id=tenant_id, edit=membership_id, _anchor="their-contacts")
    return done("Contact added to their list.", "platform.org_members", tenant_id=tenant_id, edit=membership_id, _anchor="their-contacts")


@bp.route("/org/<int:tenant_id>/contact-groups", methods=["GET"])
def org_contact_groups(tenant_id):
    open_workspace(tenant_id)
    require_area("contacts")
    choices = audience_choices(tenant_id)
    rows = []
    for grp in contact_groups.groups(tenant_id):
        given = "Not given to anyone yet" if grp["audience"] is None else audience_text(grp["audience"], choices)
        rows.append(dict(grp, given=given))
    editing = next((r for r in rows if str(r["id"]) == request.args.get("group")), None)
    tag_counts = content.member_tags(tenant_id)
    for r in rows:
        r["people"] = 0 if r["audience"] is None else sum(1 for tags in tag_counts.values() if content.matches(r["audience"], tags))
    return page("platform/org/contact_groups.html", rows=rows, editing=editing, choices=choices,
                all_contacts=content.contacts(tenant_id),
                editing_audience=content.audience_roles(editing["audience"]) if editing and editing["audience"] else [])


@bp.route("/org/<int:tenant_id>/contact-groups", methods=["POST"])
def org_contact_group_save(tenant_id):
    open_workspace(tenant_id)
    require_area("contacts")
    group_id = int(request.form["group_id"]) if str(request.form.get("group_id") or "").isdigit() else None
    try:
        new_id = contact_groups.save(g.principal, tenant_id, request.form.get("name"), group_id)
    except (ValueError, content.NotAllowed) as e:
        return failed(str(e), "platform.org_contact_groups", tenant_id=tenant_id)
    return done("Contact group saved. Now add contacts to it and choose who gets it.", "platform.org_contact_groups",
                tenant_id=tenant_id, group=new_id, _anchor="edit-group")


@bp.route("/org/<int:tenant_id>/contact-groups/<int:group_id>", methods=["POST"])
def org_contact_group_update(tenant_id, group_id):
    open_workspace(tenant_id)
    require_area("contacts")
    f = request.form
    try:
        contact_groups.update(g.principal, tenant_id, group_id, f.getlist("contacts"), f.getlist("audience"),
                              given=f.get("given") == "1")
    except (ValueError, content.NotAllowed) as e:
        return failed(str(e), "platform.org_contact_groups", tenant_id=tenant_id, group=group_id, _anchor="edit-group")
    return done("Contact group saved: everyone it's given to has its contacts in their list now.", "platform.org_contact_groups",
                tenant_id=tenant_id, group=group_id, _anchor="edit-group")


@bp.route("/org/<int:tenant_id>/contact-groups/<int:group_id>/delete", methods=["POST"])
def org_contact_group_delete(tenant_id, group_id):
    open_workspace(tenant_id)
    require_area("contacts")
    try:
        contact_groups.delete(g.principal, tenant_id, group_id)
    except content.NotAllowed as e:
        return failed(str(e), "platform.org_contact_groups", tenant_id=tenant_id)
    return done("Contact group deleted. Its contacts are still in Contacts.", "platform.org_contact_groups", tenant_id=tenant_id)


@bp.route("/org/<int:tenant_id>/contact-lists", methods=["GET", "POST"])
def org_contact_lists(tenant_id):
    """Default contact lists: which contacts each role, department or group gets in their own lists."""
    open_workspace(tenant_id)
    if "contacts" not in g.org_areas and "emergency" not in g.org_areas:
        abort(403)
    choices = audience_choices(tenant_id)
    departments = [(f"d:{d['id']}", d["name"]) for d in structure.departments(tenant_id)]
    groups = [(f"g:{x['id']}", x["name"]) for x in structure.groups(tenant_id)]
    every = dict(choices + departments + groups)
    tag = request.values.get("for") or (choices[0][0] if choices else "member")
    if tag not in every:
        tag = choices[0][0] if choices else "member"
    if request.method == "POST":
        try:
            changed = content.set_list_defaults(g.principal, tenant_id, tag, request.form.getlist("contacts"))
        except (ValueError, content.NotAllowed) as e:
            return failed(str(e), "platform.org_contact_lists", tenant_id=tenant_id, **{"for": tag})
        return done(f"Default list for {every[tag]} saved." if changed else "Nothing changed.",
                    "platform.org_contact_lists", tenant_id=tenant_id, **{"for": tag})
    rows = content.list_defaults(tenant_id, tag)
    people = len([p for p, tags in content.member_tags(tenant_id).items() if tag in tags])
    return page("platform/org/contact_lists.html", rows=rows, tag=tag, tag_name=every[tag], roles=choices,
                departments=departments, groups=groups, people=people)


@bp.route("/org/<int:tenant_id>/members/<int:membership_id>/approve", methods=["POST"])
def org_member_approve(tenant_id, membership_id):
    open_workspace(tenant_id)
    require_area("members")
    try:
        joining.approve(g.principal, tenant_id, membership_id, request.form.get("role") or "member", bool(request.form.get("notify")))
    except (ValueError, content.NotAllowed, rbac.PermissionDenied) as e:
        return failed(str(e), "platform.org_members", tenant_id=tenant_id)
    return done("Request approved: they're a member now.", "platform.org_members", tenant_id=tenant_id)


@bp.route("/org/<int:tenant_id>/members/<int:membership_id>/decline", methods=["POST"])
def org_member_decline(tenant_id, membership_id):
    open_workspace(tenant_id)
    require_area("members")
    try:
        joining.decline(g.principal, tenant_id, membership_id, bool(request.form.get("notify")))
    except (ValueError, content.NotAllowed) as e:
        return failed(str(e), "platform.org_members", tenant_id=tenant_id)
    return done("Request declined.", "platform.org_members", tenant_id=tenant_id)


@bp.route("/org/<int:tenant_id>/members/<int:membership_id>/readd", methods=["POST"])
def org_member_readd(tenant_id, membership_id):
    open_workspace(tenant_id)
    require_area("members")
    try:
        content.readd_member(g.principal, tenant_id, membership_id)
        if request.form.get("notify"):
            snap = change_notices.member_snapshot(tenant_id, membership_id)
            change_notices.member_added(g.principal, tenant_id, snap["phone"], snap["role"])
    except (ValueError, content.NotAllowed, rbac.PermissionDenied) as e:
        return failed(str(e), "platform.org_members", tenant_id=tenant_id)
    return done("Added back as a member. Change their role with Edit if needed.", "platform.org_members", tenant_id=tenant_id)


@bp.route("/org/<int:tenant_id>/members/<int:membership_id>/remove", methods=["POST"])
def org_member_remove(tenant_id, membership_id):
    open_workspace(tenant_id)
    require_area("members")
    member = TenantRepo(tenant_id).one("memberships", "id = ?", (membership_id,))
    phone = member["user_phone"] if member else None
    try:
        content.remove_member(g.principal, tenant_id, membership_id)
    except (ValueError, content.NotAllowed, rbac.PermissionDenied) as e:
        return failed(str(e), "platform.org_members", tenant_id=tenant_id)
    message = "Member removed."
    if phone and request.form.get("notify") and change_notices.member_removed(g.principal, tenant_id, phone):
        message += " They were notified in the app."
    return done(message, "platform.org_members", tenant_id=tenant_id)


# ---------------------------------------------------------------- groups and departments

@bp.route("/org/<int:tenant_id>/groups", methods=["GET"])
def org_structure(tenant_id):
    open_workspace(tenant_id)
    require_area("structure")
    edition = g.org["edition"]
    depts, grps = structure.departments(tenant_id), structure.groups(tenant_id)
    every = [d["name"] for d in depts] + [x["name"] for x in grps]
    editing_dept = next((d for d in depts if str(d["id"]) == request.args.get("dept")), None)
    editing_group = next((x for x in grps if str(x["id"]) == request.args.get("group")), None)
    return page("platform/org/structure.html", departments=depts, groups=grps, kinds=structure.GROUP_KINDS,
                dept_suggestions=suggestions.department_suggestions(edition, every)[:16],
                group_suggestions=suggestions.group_suggestions(edition, every)[:16],
                editing_dept=editing_dept, editing_group=editing_group)


def _structure_action(tenant_id, fn, message):
    open_workspace(tenant_id)
    require_area("structure")
    try:
        fn()
    except (ValueError, content.NotAllowed) as e:
        return failed(str(e), "platform.org_structure", tenant_id=tenant_id)
    return done(message, "platform.org_structure", tenant_id=tenant_id)


def _int_or_none(value):
    return int(value) if str(value or "").isdigit() else None


@bp.route("/org/<int:tenant_id>/departments", methods=["POST"])
def org_department_save(tenant_id):
    f = request.form
    dept_id = _int_or_none(f.get("department_id"))
    return _structure_action(tenant_id, lambda: structure.save_department(
        g.principal, tenant_id, f.get("name"), _int_or_none(f.get("parent_id")), dept_id),
        "Department saved." if dept_id else f"Department {' '.join((f.get('name') or '').split())} added.")


@bp.route("/org/<int:tenant_id>/departments/<int:department_id>/delete", methods=["POST"])
def org_department_delete(tenant_id, department_id):
    return _structure_action(tenant_id, lambda: structure.delete_department(g.principal, tenant_id, department_id),
                             "Department deleted. Its members are still in the organisation.")


@bp.route("/org/<int:tenant_id>/groups", methods=["POST"])
def org_group_save(tenant_id):
    f = request.form
    group_id = _int_or_none(f.get("group_id"))
    return _structure_action(tenant_id, lambda: structure.save_group(
        g.principal, tenant_id, f.get("name"), f.get("kind") or "custom", _int_or_none(f.get("department_id")), group_id),
        "Group saved." if group_id else f"Group {' '.join((f.get('name') or '').split())} added.")


@bp.route("/org/<int:tenant_id>/groups/<int:group_id>/delete", methods=["POST"])
def org_group_delete(tenant_id, group_id):
    return _structure_action(tenant_id, lambda: structure.delete_group(g.principal, tenant_id, group_id),
                             "Group deleted. Its members are still in the organisation.")


# ---------------------------------------------------------------- reminders (SMS and app)

def _placement_choices(tenant_id):
    return ([(f"d:{d['id']}", d["name"]) for d in structure.departments(tenant_id)],
            [(f"g:{x['id']}", x["name"]) for x in structure.groups(tenant_id)])


@bp.route("/org/<int:tenant_id>/reminders", methods=["GET"])
def org_reminders(tenant_id):
    open_workspace(tenant_id)
    require_area("reminders")
    prefill = {}
    if request.args.get("notice", "").isdigit():
        n = content.notice(tenant_id, int(request.args["notice"]))
        if n:
            prefill = {"title": n["title"][:reminders.TITLE_MAX], "body": n["body"][:reminders.BODY_MAX], "notice_id": n["id"],
                       "audience": content.audience_roles(n["audience"])}
    elif request.args.get("again", "").isdigit():
        r = reminders.reminder(tenant_id, int(request.args["again"]))
        if r:
            prefill = {"title": r["title"], "body": r["body"], "channels": r["channels"], "placement": r["placement"],
                       "audience": content.audience_roles(r["audience"])}
    departments, groups = _placement_choices(tenant_id)
    choices = audience_choices(tenant_id)
    names = dict(choices)
    rows = []
    for r in reminders.reminders(tenant_id):
        who = audience_text(r["audience"], choices)
        if r["placement"]:
            label = dict(departments + groups).get(r["placement"], "a removed group")
            who = f"{who} in {label}"
        rows.append(dict(r, who=who))
    today = datetime.datetime.now(content.EAT)
    return page("platform/org/reminders.html", rows=rows, choices=choices, names=names, prefill=prefill,
                departments=departments, groups=groups, channels=reminders.CHANNELS,
                min_time=today.strftime("%Y-%m-%dT%H:%M"), sms_ready=reminders.messaging.sms is not None,
                title_max=reminders.TITLE_MAX, body_max=reminders.BODY_MAX)


@bp.route("/org/<int:tenant_id>/reminders", methods=["POST"])
def org_reminder_send(tenant_id):
    open_workspace(tenant_id)
    require_area("reminders")
    form = {**request.form.to_dict(), "audience": request.form.getlist("audience")}
    try:
        reminder_id = reminders.create(g.principal, tenant_id, form)
    except (ValueError, reminders.NotAllowed, content.NotAllowed) as e:
        return failed(str(e), "platform.org_reminders", tenant_id=tenant_id)
    row = reminders.reminder(tenant_id, reminder_id)
    if row["status"] == "sent":
        parts = []
        if row["app_sent"]:
            parts.append(f"{row['app_sent']} in the app")
        if row["sms_sent"]:
            parts.append(f"{row['sms_sent']} by SMS")
        message = "Reminder sent: " + (" and ".join(parts) if parts else "nobody could be reached") + "."
        if row["note"]:
            message += f" ({row['note']}.)"
    else:
        message = "Reminder scheduled for " + datetime.datetime.fromtimestamp(row["send_at"], content.EAT).strftime("%a %d %b %Y, %H:%M") + "."
    return done(message, "platform.org_reminders", tenant_id=tenant_id)


@bp.route("/org/<int:tenant_id>/reminders/estimate", methods=["POST"])
def org_reminder_estimate(tenant_id):
    open_workspace(tenant_id)
    require_area("reminders")
    body = request.get_json(silent=True) or {}
    try:
        audience = content.parse_audience(body.get("audience") or [], tenant_id)
    except ValueError:
        audience = ""
    return jsonify(reminders.estimate(tenant_id, audience, str(body.get("placement") or ""), body.get("channels") or "both",
                                      str(body.get("title") or ""), str(body.get("body") or "")))


@bp.route("/org/<int:tenant_id>/reminders/<int:reminder_id>/cancel", methods=["POST"])
def org_reminder_cancel(tenant_id, reminder_id):
    open_workspace(tenant_id)
    require_area("reminders")
    try:
        reminders.cancel(g.principal, tenant_id, reminder_id)
    except (ValueError, reminders.NotAllowed) as e:
        return failed(str(e), "platform.org_reminders", tenant_id=tenant_id)
    return done("Reminder cancelled. It won't be sent.", "platform.org_reminders", tenant_id=tenant_id)


# ---------------------------------------------------------------- preview: what members see on USSD and in the app

def _preview_role(tenant_id):
    role = request.values.get("role") or (request.get_json(silent=True) or {}).get("role") or "member"
    return role if role in dict(audience_choices(tenant_id)) else "member"


@bp.route("/org/<int:tenant_id>/preview", methods=["GET"])
def org_preview(tenant_id):
    t, _areas = open_workspace(tenant_id)
    require_area("preview")
    role = _preview_role(tenant_id)
    allocations = ussd_codes.allocations(tenant_id)
    contacts = content.visible_contacts(tenant_id, None, emergency=False, roles={role})
    grouped = {}
    for c in contacts:
        grouped.setdefault(c["group_name"] or "Contacts", []).append(c)
    return page("platform/org/preview.html", role=role, choices=audience_choices(tenant_id),
                notices=content.visible_notices(tenant_id, None, roles={role}),
                emergency=content.visible_contacts(tenant_id, None, emergency=True, roles={role}), grouped=grouped,
                dial=ussd_codes.dial_string(allocations[0]) if allocations else None, main_code=ussd_codes.main_code())


@bp.route("/org/<int:tenant_id>/preview/ussd", methods=["POST"])
def org_preview_ussd(tenant_id):
    t, _areas = open_workspace(tenant_id)
    require_area("preview")
    body = request.get_json(silent=True) or {}
    inputs = [str(x).strip()[:160] for x in (body.get("inputs") or [])][:30]
    screen_text = ussd_orgs.org_menu({"tenant_id": tenant_id, "name": t["name"]}, "preview", "preview", inputs,
                                     ussd_orgs.preview_contact_menu, roles={_preview_role(tenant_id)}, simulate=True)
    return jsonify({"screen": screen_text[4:], "end": screen_text.startswith("END ")})


@bp.route("/org/<int:tenant_id>/preview/notice", methods=["POST"])
def org_preview_notice(tenant_id):
    """A draft notice as USSD screens (the list line and each page) for the live preview on the Notices form."""
    t, _areas = open_workspace(tenant_id)
    body = request.get_json(silent=True) or {}
    draft = {"title": str(body.get("title") or "Your title")[:content.NOTICE_TITLE_MAX],
             "body": str(body.get("body") or "Your message")[:content.NOTICE_BODY_MAX], "is_emergency": 1 if body.get("urgent") else 0}
    org = {"tenant_id": tenant_id, "name": t["name"]}
    pages = len(ussd_orgs.text_pages(f"{draft['title']}\n{draft['body']}"))
    screens = [ussd_orgs.notices_screen(org, [draft], "preview", [], simulate=True)[4:]]
    for k in range(pages):
        screens.append(ussd_orgs.notices_screen(org, [draft], "preview", ["1"] + ["98"] * k, simulate=True)[4:])
    return jsonify({"screens": screens, "pages": pages})


# ---------------------------------------------------------------- history: deleted and expired items

@bp.route("/org/<int:tenant_id>/history", methods=["GET"])
def org_history(tenant_id):
    open_workspace(tenant_id)
    require_area("history")
    return page("platform/org/history.html", history=content.history(tenant_id), expiry_choices=content.EXPIRY_CHOICES,
                can_notices="notices" in g.org_areas, can_contacts="contacts" in g.org_areas,
                can_emergency="emergency" in g.org_areas, today=datetime.datetime.now(content.EAT).date().isoformat())


def _history_action(tenant_id, fn, message):
    open_workspace(tenant_id)
    require_area("history")
    try:
        fn()
    except (ValueError, content.NotAllowed) as e:
        return failed(str(e), "platform.org_history", tenant_id=tenant_id)
    return done(message, "platform.org_history", tenant_id=tenant_id)


@bp.route("/org/<int:tenant_id>/history/notices/<int:notice_id>/repost", methods=["POST"])
def org_notice_repost(tenant_id, notice_id):
    form = request.form.to_dict()
    return _history_action(tenant_id, lambda: content.repost_notice(g.principal, tenant_id, notice_id, form),
                           "Notice reposted. Members see it again now.")


@bp.route("/org/<int:tenant_id>/history/contacts/<int:contact_id>/restore", methods=["POST"])
def org_contact_restore(tenant_id, contact_id):
    return _history_action(tenant_id, lambda: content.restore_contact(g.principal, tenant_id, contact_id),
                           "Contact restored. Members can find it again.")


@bp.route("/org/<int:tenant_id>/history/<any(notices, org_contacts):table>/<int:item_id>/purge", methods=["POST"])
def org_history_purge(tenant_id, table, item_id):
    return _history_action(tenant_id, lambda: content.purge(g.principal, tenant_id, table, item_id), "Deleted for good.")


# ---------------------------------------------------------------- roles and titles (the organisation's own lists)

def _role_summary(role_id):
    from pb.rbac.catalogue import TENANT_PERMISSIONS
    perms = sorted(rbac.role_permissions(role_id))
    readable = [TENANT_PERMISSIONS[p].split(" (")[0] for p in perms if p in TENANT_PERMISSIONS]
    return readable


@bp.route("/org/<int:tenant_id>/lists", methods=["GET"])
def org_lists(tenant_id):
    open_workspace(tenant_id)
    require_area("lists")
    counts = {}
    for a in rbac.tenant_assignments(tenant_id):
        counts[a["key"]] = counts.get(a["key"], 0) + 1
    roles = []
    for key, name in tenancy.available_roles(tenant_id):
        role = rbac.role_by_key(key, tenant_id)
        roles.append({"id": role["id"], "key": key, "name": name, "own": not role["is_system"], "people": counts.get(key, 0),
                      "can": _role_summary(role["id"])})
    from pb.rbac.catalogue import TENANT_PERMISSIONS
    own_titles = tenancy.org_titles(tenant_id)
    members = content.member_rows(tenant_id)
    return page("platform/org/lists.html", roles=roles, own_titles=own_titles,
                title_suggestions=[t for t in suggestions.title_options(g.org["edition"]) if t.lower() not in {o.lower() for o in own_titles}][:24],
                used_titles=sorted({m["title"] for m in members if m["title"] and m["title"].lower() not in {o.lower() for o in own_titles}}),
                base_roles=[(k, n) for k, n in tenancy.available_roles(tenant_id) if k != tenancy.ADMIN],
                can_customise=tenancy.plan_allows(tenant_id, "custom_roles"),
                permissions=[(k, v) for k, v in TENANT_PERMISSIONS.items() if rbac.can(g.principal, k, tenant_id) or g.org_staff_help])


def _lists_action(tenant_id, fn, message):
    open_workspace(tenant_id)
    require_area("lists")
    try:
        fn()
    except (ValueError, content.NotAllowed, rbac.PermissionDenied) as e:
        return failed(str(e), "platform.org_lists", tenant_id=tenant_id)
    return done(message, "platform.org_lists", tenant_id=tenant_id)


@bp.route("/org/<int:tenant_id>/lists/roles", methods=["POST"])
def org_role_add(tenant_id):
    f = request.form
    perms = f.getlist("permissions") if f.get("customise") else None
    return _lists_action(tenant_id, lambda: content.add_role(g.principal, tenant_id, f.get("name"), f.get("like"), perms),
                         f"Role {' '.join((f.get('name') or '').split())} added. You can now give it to people on the Members page.")


@bp.route("/org/<int:tenant_id>/lists/roles/<int:role_id>/delete", methods=["POST"])
def org_role_delete(tenant_id, role_id):
    return _lists_action(tenant_id, lambda: content.delete_role(g.principal, tenant_id, role_id), "Role removed.")


@bp.route("/org/<int:tenant_id>/lists/titles", methods=["POST"])
def org_titles_save(tenant_id):
    f = request.form
    current = tenancy.org_titles(tenant_id)
    if f.get("remove"):
        new = [t for t in current if t != f.get("remove")]
        message = f"Removed {f.get('remove')} from your titles."
    else:
        added = [t.strip() for t in (f.get("add") or "").split(",") if t.strip()]
        if not added:
            return failed("Type a title to add.", "platform.org_lists", tenant_id=tenant_id)
        new = current + added
        message = "Added to your titles: " + ", ".join(added) + "."
    return _lists_action(tenant_id, lambda: tenancy.set_org_titles(g.principal, tenant_id, new), message)


# ---------------------------------------------------------------- settings

@bp.route("/org/<int:tenant_id>/settings", methods=["GET"])
def org_settings(tenant_id):
    open_workspace(tenant_id)
    require_area("settings")
    members = content.member_rows(tenant_id)
    titles = suggestions.title_options(g.org["edition"], [m["title"] for m in members if m["title"]])
    return page("platform/org/settings.html", mode=tenancy.get_setting(tenant_id, "allow_other_phone"),
                join_mode=tenancy.get_setting(tenant_id, "join_mode"), org_code=g.org["org_code"],
                member_contacts=tenancy.get_setting(tenant_id, "member_contacts"),
                rules=tenancy.other_phone_rules(tenant_id), choices=audience_choices(tenant_id),
                departments=structure.departments(tenant_id), titles=titles)


@bp.route("/org/<int:tenant_id>/settings", methods=["POST"])
def org_settings_save(tenant_id):
    open_workspace(tenant_id)
    require_area("settings")
    f = request.form
    if f.get("key") in ("join_mode", "member_contacts"):
        try:
            tenancy.set_setting(g.principal, tenant_id, f.get("key"), f.get("value"))
        except ValueError as e:
            return failed(str(e), "platform.org_settings", tenant_id=tenant_id)
        return done("Saved.", "platform.org_settings", tenant_id=tenant_id)
    if f.get("key") != "allow_other_phone":
        abort(400)
    try:
        tenancy.set_other_phone_rules(g.principal, tenant_id, f.get("value"), f.getlist("roles"), f.getlist("departments"),
                                      f.getlist("titles") + [t.strip() for t in (f.get("other_titles") or "").split(",")])
    except ValueError as e:
        return failed(str(e), "platform.org_settings", tenant_id=tenant_id)
    return done("Saved.", "platform.org_settings", tenant_id=tenant_id)
