"""The organisation workspace (/platform/org): admins and operators publish what their members see.

Notices, contacts, emergency contacts and members. Each action is permission-checked in pb.org.content, so a
supervisor or publisher only gets the parts their role allows. Platform staff who manage organisations can open
any workspace (from the organisation's page) to help set it up; everything is in the audit log.
"""
import time

from flask import abort, g, redirect, render_template, request, url_for

from pb.billing import ussd_codes
from pb.org import content
from pb.portal import bp, done, failed, is_staff
from pb.rbac import engine as rbac
from pb.tenancy import tenants as tenancy

AREAS = {  # area: (permission, module)
    "notices": ("announcements.publish", "announcements"),
    "contacts": ("contacts.manage", "directory"),
    "emergency": ("emergency.manage", "emergency"),
    "members": ("members.manage", "members"),
    "settings": ("tenant.settings.manage", None),
}
AUDIENCE_ROLES = ["member", "guardian", "client", "teacher", "content_publisher", "department_supervisor", "org_admin", "auditor"]


def workspaces():
    """Organisations the current person can manage something in."""
    if is_staff():
        return []
    rows = []
    for org in tenancy.memberships_of(g.staff["phone"]):
        areas = [a for a, (perm, _m) in AREAS.items() if rbac.can(g.principal, perm, org["tenant_id"])]
        if areas:
            rows.append(dict(org, areas=areas))
    return rows


def open_workspace(tenant_id):
    """The tenant and the areas this person can use there (404 if none)."""
    t = tenancy.tenant(tenant_id)
    if not t:
        abort(404)
    staff_help = rbac.can(g.principal, "platform.tenants.manage")
    areas = [a for a, (perm, module) in AREAS.items()
             if (staff_help or rbac.can(g.principal, perm, tenant_id)) and (module is None or tenancy.module_enabled(tenant_id, module))]
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
    return page("platform/org/notices.html", rows=rows, choices=choices, editing=editing, now=int(time.time()),
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
    groups = sorted({c["group_name"] for c in rows if c["group_name"]})
    return page("platform/org/contacts.html", rows=rows, choices=choices, editing=editing, emergency=emergency, groups=groups,
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
    return page("platform/org/members.html", rows=content.member_rows(tenant_id), choices=choices)


@bp.route("/org/<int:tenant_id>/members", methods=["POST"])
def org_member_add(tenant_id):
    open_workspace(tenant_id)
    require_area("members")
    try:
        content.add_member_with_role(g.principal, tenant_id, request.form.get("phone"), request.form.get("name"),
                                     request.form.get("role") or "member")
    except (ValueError, content.NotAllowed, rbac.PermissionDenied) as e:
        return failed(str(e), "platform.org_members", tenant_id=tenant_id)
    return done("Member added. They see your notices and contacts once they log in with that number.", "platform.org_members",
                tenant_id=tenant_id)


@bp.route("/org/<int:tenant_id>/members/<int:membership_id>/remove", methods=["POST"])
def org_member_remove(tenant_id, membership_id):
    open_workspace(tenant_id)
    require_area("members")
    try:
        content.remove_member(g.principal, tenant_id, membership_id)
    except (ValueError, content.NotAllowed, rbac.PermissionDenied) as e:
        return failed(str(e), "platform.org_members", tenant_id=tenant_id)
    return done("Member removed.", "platform.org_members", tenant_id=tenant_id)


# ---------------------------------------------------------------- settings

@bp.route("/org/<int:tenant_id>/settings", methods=["GET"])
def org_settings(tenant_id):
    open_workspace(tenant_id)
    require_area("settings")
    return page("platform/org/settings.html", allow_other_phone=tenancy.allow_other_phone(tenant_id))


@bp.route("/org/<int:tenant_id>/settings", methods=["POST"])
def org_settings_save(tenant_id):
    open_workspace(tenant_id)
    require_area("settings")
    if request.form.get("key") != "allow_other_phone":
        abort(400)
    tenancy.set_setting(g.principal, tenant_id, "allow_other_phone", "1" if request.form.get("value") == "1" else "0")
    return done("Saved.", "platform.org_settings", tenant_id=tenant_id)
