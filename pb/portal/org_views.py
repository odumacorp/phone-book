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

from pb.org import content, reminders, structure, suggestions
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
    "settings": ("tenant.settings.manage", None),
}
AUDIENCE_ROLES = ["member", "guardian", "client", "teacher", "content_publisher", "department_supervisor", "org_admin", "auditor"]


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
    return render_template(template, org=g.org, areas=g.org_areas, staff_help=g.org_staff_help, page="org", **values)


def audience_choices(tenant_id):
    roles = {r["key"]: r["name"] for r in rbac.roles_for_tenant(tenant_id)}
    keys = [k for k in AUDIENCE_ROLES if k in roles] + [k for k in roles if k not in AUDIENCE_ROLES and k not in ("org_owner",)]
    return [(k, roles[k]) for k in keys]


def audience_text(stored, choices):
    names = dict(choices)
    keys = content.audience_roles(stored)
    return "Everyone" if not keys else ", ".join(names.get(k, k) for k in keys)


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
        content.save_notice(g.principal, tenant_id, form, notice_id)
    except (ValueError, content.NotAllowed) as e:
        return failed(str(e), "platform.org_notices", tenant_id=tenant_id, **({"edit": notice_id} if notice_id else {}))
    return done("Notice saved. Members see it on USSD and in the app now." if not notice_id else "Notice updated.",
                "platform.org_notices", tenant_id=tenant_id)


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
    rows = [dict(c, audience_text=audience_text(c["audience"], choices)) for c in content.contacts(tenant_id, emergency=emergency)]
    editing = content.contact(tenant_id, int(request.args["edit"])) if request.args.get("edit", "").isdigit() else None
    if editing and bool(editing["is_emergency"]) != emergency:
        editing = None
    edition = g.org["edition"]
    defined = structure.names(tenant_id)
    used_groups = sorted({c["group_name"] for c in rows if c["group_name"]} - set(defined), key=str.lower)
    every_group = set(defined) | set(used_groups)
    group_lists = [("Your departments and groups", defined), ("Used on contacts", used_groups),
                   ("Suggested for you", [x for x in suggestions.group_options(edition, []) if x not in every_group])]
    used_titles = sorted({c["title"] for c in content.contacts(tenant_id) if c["title"]}, key=str.lower)
    title_lists = [("Used on contacts", used_titles),
                   ("Suggested for you", [t for t in suggestions.title_options(edition) if t.lower() not in {u.lower() for u in used_titles}])]
    name_lists = [("Suggested", suggestions.emergency_options(edition, [c["name"] for c in rows]))] if emergency else []
    return page("platform/org/contacts.html", rows=rows, choices=choices, editing=editing, emergency=emergency,
                group_lists=group_lists, title_lists=title_lists, name_lists=name_lists,
                can_structure="structure" in g.org_areas,
                editing_audience=content.audience_roles(editing["audience"]) if editing else [],
                endpoint="platform.org_emergency" if emergency else "platform.org_contacts")


def _contact_save(tenant_id, emergency):
    open_workspace(tenant_id)
    require_area("emergency" if emergency else "contacts")
    endpoint = "platform.org_emergency" if emergency else "platform.org_contacts"
    form = {**request.form.to_dict(), "audience": request.form.getlist("audience")}
    contact_id = int(request.form["contact_id"]) if request.form.get("contact_id", "").isdigit() else None
    try:
        content.save_contact(g.principal, tenant_id, form, emergency, contact_id)
    except (ValueError, content.NotAllowed) as e:
        return failed(str(e), endpoint, tenant_id=tenant_id, **({"edit": contact_id} if contact_id else {}))
    return done("Contact updated." if contact_id else "Contact added. Members can find it on USSD and in the app now.",
                endpoint, tenant_id=tenant_id)


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

@bp.route("/org/<int:tenant_id>/members", methods=["GET"])
def org_members(tenant_id):
    open_workspace(tenant_id)
    require_area("members")
    choices = [(k, n) for k, n in audience_choices(tenant_id)]
    placements = structure.placements_by_member(tenant_id)
    rows = [dict(m, placed=placements.get(m["id"], [])) for m in content.member_rows(tenant_id)]
    editing = next((m for m in rows if str(m["id"]) == request.args.get("edit")), None)
    editing_scope = editing_placements = None
    if editing:
        _key, editing_scope = content.member_role(tenant_id, editing["user_phone"])
        editing_placements = structure.placements_of(tenant_id, editing["id"])
    used_titles = sorted({m["title"] for m in rows if m["title"]}, key=str.lower)
    title_lists = [("Used in your organisation", used_titles),
                   ("Suggested for you", [t for t in suggestions.title_options(g.org["edition"]) if t not in used_titles])]
    return page("platform/org/members.html", rows=rows, choices=choices,
                departments=structure.departments(tenant_id), groups=structure.groups(tenant_id),
                can_structure="structure" in g.org_areas, editing=editing, editing_scope=editing_scope,
                editing_placements=editing_placements or [], title_lists=title_lists,
                former=content.former_members(tenant_id), me=g.principal)


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
    return done("Member added. They see your notices and contacts once they log in with that number.", "platform.org_members",
                tenant_id=tenant_id)


@bp.route("/org/<int:tenant_id>/members/<int:membership_id>", methods=["POST"])
def org_member_update(tenant_id, membership_id):
    open_workspace(tenant_id)
    require_area("members")
    form = {**request.form.to_dict(), "placements": request.form.getlist("placements")}
    try:
        content.update_member(g.principal, tenant_id, membership_id, form)
    except (ValueError, content.NotAllowed, rbac.PermissionDenied) as e:
        message = str(e)
        if isinstance(e, rbac.PermissionDenied):
            message = "You can't give that role: it has permissions you don't have" + (
                f" ({message.split(': ', 1)[1]})." if ": " in message else ".")
        return failed(message, "platform.org_members", tenant_id=tenant_id, edit=membership_id)
    return done("Member updated.", "platform.org_members", tenant_id=tenant_id)


@bp.route("/org/<int:tenant_id>/members/<int:membership_id>/readd", methods=["POST"])
def org_member_readd(tenant_id, membership_id):
    open_workspace(tenant_id)
    require_area("members")
    try:
        content.readd_member(g.principal, tenant_id, membership_id)
    except (ValueError, content.NotAllowed, rbac.PermissionDenied) as e:
        return failed(str(e), "platform.org_members", tenant_id=tenant_id)
    return done("Added back as a member. Change their role with Edit if needed.", "platform.org_members", tenant_id=tenant_id)


@bp.route("/org/<int:tenant_id>/members/<int:membership_id>/remove", methods=["POST"])
def org_member_remove(tenant_id, membership_id):
    open_workspace(tenant_id)
    require_area("members")
    try:
        content.remove_member(g.principal, tenant_id, membership_id)
    except (ValueError, content.NotAllowed, rbac.PermissionDenied) as e:
        return failed(str(e), "platform.org_members", tenant_id=tenant_id)
    return done("Member removed.", "platform.org_members", tenant_id=tenant_id)


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


# ---------------------------------------------------------------- settings

@bp.route("/org/<int:tenant_id>/settings", methods=["GET"])
def org_settings(tenant_id):
    open_workspace(tenant_id)
    require_area("settings")
    members = content.member_rows(tenant_id)
    titles = suggestions.title_options(g.org["edition"], [m["title"] for m in members if m["title"]])
    return page("platform/org/settings.html", mode=tenancy.get_setting(tenant_id, "allow_other_phone"),
                rules=tenancy.other_phone_rules(tenant_id), choices=audience_choices(tenant_id),
                departments=structure.departments(tenant_id), titles=titles)


@bp.route("/org/<int:tenant_id>/settings", methods=["POST"])
def org_settings_save(tenant_id):
    open_workspace(tenant_id)
    require_area("settings")
    f = request.form
    if f.get("key") != "allow_other_phone":
        abort(400)
    try:
        tenancy.set_other_phone_rules(g.principal, tenant_id, f.get("value"), f.getlist("roles"), f.getlist("departments"),
                                      f.getlist("titles") + [t.strip() for t in (f.get("other_titles") or "").split(",")])
    except ValueError as e:
        return failed(str(e), "platform.org_settings", tenant_id=tenant_id)
    return done("Saved.", "platform.org_settings", tenant_id=tenant_id)
