"""Platform portal pages. Each view: check the permission (needs), validate the form, call the service, flash, redirect."""
import json

from flask import abort, g, redirect, render_template, request, url_for

from pb.audit import audit_entries
from pb.auth import staff as staff_auth
from pb.billing import catalogue, ussd_codes
from pb.db import fetch_one
from pb.features import feature_on, set_feature
from pb.messaging import sms
from pb.portal import bp, done, failed, is_staff, needs
from pb.rbac import engine as rbac
from pb.rbac.catalogue import ALL_PERMISSIONS, SYSTEM_ROLES
from pb.org import profile as org_profile
from pb.org import structure
from pb.tenancy import tenants as tenancy
from pb.audit import audit
from pb import support


def _reseller_id():
    """A reseller only sees and creates their own organisations: their staff id, or None for everyone else."""
    if rbac.can(g.principal, "platform.tenants.view") or rbac.can(g.principal, "platform.tenants.manage"):
        return None
    return g.staff["id"]


def _can_open_tenant(t):
    if rbac.can(g.principal, "platform.tenants.view") or rbac.can(g.principal, "platform.tenants.manage"):
        return True
    return rbac.can(g.principal, "platform.tenants.view_own") and g.staff and t["reseller_id"] == g.staff["id"]


# ---------------------------------------------------------------- dashboard

@bp.route("/", methods=["GET"])
def dashboard():
    if not is_staff():
        return redirect(url_for("platform.org_home"))  # organisation admins: their workspace
    counts = {}
    if rbac.can_anywhere(g.principal, "platform.tenants.view") or rbac.can(g.principal, "platform.tenants.view_own"):
        rows = tenancy.tenants(reseller_id=_reseller_id())
        for status in tenancy.STATUSES:
            counts[status] = sum(1 for t in rows if t["status"] == status)
    stats = {
        "tenants": counts,
        "codes_available": fetch_one("SELECT COUNT(*) AS n FROM ussd_codes WHERE status = 'available'")["n"],
        "staff": fetch_one("SELECT COUNT(*) AS n FROM staff_users WHERE active = 1")["n"],
        "main_code": ussd_codes.main_code(),
        "otp": feature_on("otp"),
    }
    return render_template("platform/dashboard.html", page="dashboard", stats=stats, statuses=tenancy.STATUSES,
                           no_staff=stats["staff"] == 0 and g.principal == rbac.ROOT)


# ---------------------------------------------------------------- organisations

@bp.route("/tenants", methods=["GET"])
@needs("platform.tenants.view", "platform.tenants.manage", "platform.tenants.view_own")
def tenants_list():
    status = request.args.get("status") or None
    query = (request.args.get("q") or "").strip() or None
    from pb.org.profile import logo_url
    rows = [dict(t, logo_url=logo_url(t["id"])) for t in tenancy.tenants(status=status, reseller_id=_reseller_id(), query=query)]
    return render_template(
        "platform/tenants.html", page="tenants", rows=rows, status=status, q=query or "", statuses=tenancy.STATUSES,
        editions=tenancy.editions(active_only=True), plans=catalogue.plans(audience="enterprise", active_only=False),
        can_create=rbac.can(g.principal, "platform.tenants.manage") or rbac.can(g.principal, "platform.tenants.create_own"),
    )


@bp.route("/tenants", methods=["POST"])
@needs("platform.tenants.manage", "platform.tenants.create_own")
def tenants_create():
    reseller = None if rbac.can(g.principal, "platform.tenants.manage") else g.staff["id"]
    try:
        tenant_id = tenancy.create_tenant(g.principal, request.form.get("name"), request.form.get("edition"),
                                          request.form.get("plan"), reseller_id=reseller)
        owner = (request.form.get("owner_phone") or "").strip()
        if owner:
            tenancy.appoint_owner(g.principal, tenant_id, owner, request.form.get("owner_name"))
    except (ValueError, rbac.PermissionDenied) as e:
        return failed(str(e), "platform.tenants_list")
    return done("Organisation created (trial).", "platform.tenant_detail", tenant_id=tenant_id)


@bp.route("/tenants/<int:tenant_id>", methods=["GET"])
@needs("platform.tenants.view", "platform.tenants.manage", "platform.tenants.view_own")
def tenant_detail(tenant_id):
    t = tenancy.tenant(tenant_id)
    if not t or not _can_open_tenant(t):
        abort(404)
    allocations = ussd_codes.allocations(tenant_id)
    member_total = len(tenancy.members(tenant_id, limit=100000))
    plan_row = catalogue.plan(t["plan_key"]) if t["plan_key"] else None
    price = catalogue.quote(plan_row, member_total, catalogue.plan_tiers(plan_row["key"])) if plan_row else None
    return render_template(
        "platform/tenant.html", page="tenants", t=t, profile=org_profile.get(tenant_id), statuses=tenancy.STATUSES,
        price=price, kes=catalogue.kes, modules=tenancy.tenant_modules(tenant_id),
        settings=tenancy.settings(tenant_id), setting_help=tenancy.SETTINGS, setting_choices=tenancy.SETTING_CHOICES,
        plans=catalogue.plans(audience="enterprise"), editions=tenancy.editions(),
        members=tenancy.members(tenant_id, limit=50), member_count=len(tenancy.members(tenant_id, limit=100000)),
        dept_names={d["id"]: d["name"] for d in structure.departments(tenant_id)},
        group_names={x["id"]: x["name"] for x in structure.groups(tenant_id)},
        member_ids={m["user_phone"]: m["id"] for m in tenancy.members(tenant_id, limit=100000)},
        member_names={m["user_phone"]: m["display_name"] for m in tenancy.members(tenant_id, limit=100000)},
        assignments=rbac.tenant_assignments(tenant_id), roles=rbac.roles_for_tenant(tenant_id),
        allocations=allocations, dial=[ussd_codes.dial_string(a) for a in allocations],
        codes=[c for c in ussd_codes.codes() if c["status"] != "retired" and (c["code_type"] == "shared" or not c["tenants"])],
        audit_rows=audit_entries(tenant_id=tenant_id, limit=30),
        manage=rbac.can(g.principal, "platform.tenants.manage"),
        manage_codes=rbac.can(g.principal, "platform.ussd_codes.manage"),
    )


def _tenant_action(tenant_id, fn, message):
    if not tenancy.tenant(tenant_id):
        abort(404)
    try:
        fn()
    except (ValueError, rbac.PermissionDenied) as e:
        return failed(str(e), "platform.tenant_detail", tenant_id=tenant_id)
    return done(message, "platform.tenant_detail", tenant_id=tenant_id)


@bp.route("/tenants/<int:tenant_id>/update", methods=["POST"])
@needs("platform.tenants.manage")
def tenant_update(tenant_id):
    f = request.form
    values = {k: f[k].strip() for k in ("name", "status", "plan_key") if f.get(k)}
    return _tenant_action(tenant_id, lambda: tenancy.update_tenant(g.principal, tenant_id, **values), "Organisation updated.")


@bp.route("/tenants/<int:tenant_id>/edition", methods=["POST"])
@needs("platform.tenants.manage")
def tenant_edition(tenant_id):
    def apply():
        key = request.form.get("edition")
        if not tenancy.edition(key):
            raise ValueError("Choose an edition.")
        tenancy.update_tenant(g.principal, tenant_id, edition=key)
        tenancy.apply_edition(g.principal, tenant_id, key, tenancy.tenant(tenant_id)["plan_key"])
    return _tenant_action(tenant_id, apply, "Edition applied: its modules are switched on.")


@bp.route("/tenants/<int:tenant_id>/module", methods=["POST"])
@needs("platform.tenants.manage")
def tenant_module(tenant_id):
    key, on = request.form.get("module"), request.form.get("on") == "1"
    return _tenant_action(tenant_id, lambda: tenancy.set_module(g.principal, tenant_id, key, on), f"Module {'on' if on else 'off'}.")


@bp.route("/tenants/<int:tenant_id>/setting", methods=["POST"])
@needs("platform.tenants.manage")
def tenant_setting(tenant_id):
    key, value = request.form.get("key"), request.form.get("value")
    return _tenant_action(tenant_id, lambda: tenancy.set_setting(g.principal, tenant_id, key, value), "Setting saved.")


@bp.route("/tenants/<int:tenant_id>/owner", methods=["POST"])
@needs("platform.tenants.manage")
def tenant_owner(tenant_id):
    phone, name, notify = request.form.get("phone"), request.form.get("name"), request.form.get("notify")

    def appoint():
        tenancy.appoint_owner(g.principal, tenant_id, phone, name)
        if notify:
            from pb.org import change_notices
            from pb.text import login_phone
            change_notices.member_added(g.principal, tenant_id, login_phone(phone), "org_owner")
    return _tenant_action(tenant_id, appoint, "Super Admin appointed.")


@bp.route("/tenants/<int:tenant_id>/revoke", methods=["POST"])
@needs("platform.tenants.manage")
def tenant_revoke(tenant_id):
    assignment = int(request.form.get("assignment") or 0)

    def revoke():
        row = next((a for a in rbac.tenant_assignments(tenant_id) if a["id"] == assignment), None)
        if row and row["key"] == tenancy.SUPER_ADMIN:
            raise ValueError("Every organisation needs its Super Admin. Appoint someone else as Super Admin instead.")
        rbac.revoke(g.principal, tenant_id, assignment, as_platform=True)
    return _tenant_action(tenant_id, revoke, "Role removed.")


@bp.route("/tenants/<int:tenant_id>/code", methods=["POST"])
@needs("platform.ussd_codes.manage")
def tenant_code(tenant_id):
    code_id = int(request.form.get("code_id") or 0)
    org_code = (request.form.get("org_code") or "").strip() or None
    return _tenant_action(tenant_id, lambda: ussd_codes.assign(g.principal, tenant_id, code_id, org_code), "USSD code assigned.")


@bp.route("/tenants/<int:tenant_id>/code/release", methods=["POST"])
@needs("platform.ussd_codes.manage")
def tenant_code_release(tenant_id):
    allocation = int(request.form.get("allocation") or 0)
    return _tenant_action(tenant_id, lambda: ussd_codes.release(g.principal, allocation), "USSD code released.")


# ---------------------------------------------------------------- prices

@bp.route("/pricing", methods=["GET"])
@needs("platform.pricing.view", "platform.pricing.manage")
def pricing():
    return render_template("platform/pricing.html", page="pricing", items=catalogue.price_list(),
                           manage=rbac.can(g.principal, "platform.pricing.manage"),
                           audiences=catalogue.AUDIENCES, models=catalogue.MODELS)


@bp.route("/pricing/<kind>/<key>", methods=["GET"])
@needs("platform.pricing.view", "platform.pricing.manage")
def price_edit(kind, key):
    manage = rbac.can(g.principal, "platform.pricing.manage")
    history = audit_entries(action="pricing.", limit=500)
    history = [h for h in history if h["target_id"] == key][:20]
    for h in history:
        h["changes"] = json.loads(h["details"]) if h["details"] else {}
    common = dict(page="pricing", manage=manage, history=history, kind=kind)
    if kind == "plan":
        item = catalogue.plan(key)
        if not item:
            abort(404)
        return render_template("platform/price_plan.html", item=item, features=catalogue.plan_features(key),
                               tiers=catalogue.plan_tiers(key), models=catalogue.MODELS, intervals=catalogue.INTERVALS, **common)
    if kind == "addon":
        item = catalogue.addon(key)
        if not item:
            abort(404)
        return render_template("platform/price_addon.html", item=item, intervals=catalogue.INTERVALS, **common)
    if kind == "code_fee":
        item = catalogue.code_fee(key)
        if not item:
            abort(404)
        return render_template("platform/price_code_fee.html", item=item, **common)
    abort(404)


def _price_action(kind, key, fn, message="Saved."):
    try:
        changes = fn()
    except ValueError as e:
        return failed(str(e), "platform.price_edit", kind=kind, key=key)
    if changes == {}:
        message = "Nothing changed."
    return done(message, "platform.price_edit", kind=kind, key=key)


@bp.route("/pricing/plan/<key>", methods=["POST"])
@needs("platform.pricing.manage")
def price_plan_save(key):
    f = request.form

    def save():
        model = f.get("model")
        if model not in catalogue.MODELS:
            raise ValueError("Choose a pricing model.")
        interval = f.get("billing_interval") or None
        if interval and interval not in catalogue.INTERVALS:
            raise ValueError("Choose a billing interval.")
        quoted = model == "quoted"
        return catalogue.update_plan(
            key, g.principal,
            name=" ".join((f.get("name") or "").split())[:80] or catalogue.plan(key)["name"],
            model=model, billing_interval=interval,
            base_price=None if quoted else catalogue.parse_kes(f.get("base_price"), allow_empty=True),
            included_members=catalogue.parse_int(f.get("included_members")),
            per_member_price=catalogue.parse_kes(f.get("per_member_price"), allow_empty=True),
            annual_discount_pct=catalogue.parse_int(f.get("annual_discount_pct"), allow_empty=False, high=90),
            trial_days=catalogue.parse_int(f.get("trial_days"), allow_empty=False, high=365),
            active=1 if f.get("active") == "1" else 0,
            sort=catalogue.parse_int(f.get("sort"), allow_empty=False, high=10000),
        )
    return _price_action("plan", key, save)


@bp.route("/pricing/plan/<key>/feature", methods=["POST"])
@needs("platform.pricing.manage")
def price_plan_feature(key):
    feature = (request.form.get("feature") or "").strip()
    value = None if request.form.get("delete") == "1" else (request.form.get("value") or "").strip()[:40]
    return _price_action("plan", key, lambda: catalogue.set_plan_feature(key, feature, value, g.principal), "Feature saved.")


@bp.route("/pricing/plan/<key>/tier", methods=["POST"])
@needs("platform.pricing.manage")
def price_plan_tier(key):
    f = request.form
    if f.get("delete"):
        return _price_action("plan", key, lambda: catalogue.delete_tier(key, int(f.get("delete")), g.principal), "Tier removed.")
    return _price_action("plan", key, lambda: catalogue.add_tier(
        key, catalogue.parse_int(f.get("from_members"), allow_empty=False, low=1),
        catalogue.parse_kes(f.get("per_member_price")), g.principal), "Tier added.")


@bp.route("/pricing/plans/new", methods=["POST"])
@needs("platform.pricing.manage")
def price_plan_new():
    f = request.form
    key = (f.get("key") or "").strip()
    try:
        catalogue.create_plan(g.principal, key, " ".join((f.get("name") or "").split())[:80] or key, f.get("audience"), f.get("model"))
    except ValueError as e:
        return failed(str(e), "platform.pricing")
    return done("Plan added (inactive). Set its prices, then make it active.", "platform.price_edit", kind="plan", key=key)


@bp.route("/pricing/addon/<key>", methods=["POST"])
@needs("platform.pricing.manage")
def price_addon_save(key):
    f = request.form

    def save():
        interval = f.get("billing_interval") or None
        if interval and interval not in catalogue.INTERVALS:
            raise ValueError("Choose a billing interval.")
        return catalogue.update_addon(
            key, g.principal, name=" ".join((f.get("name") or "").split())[:80] or catalogue.addon(key)["name"],
            price=catalogue.parse_kes(f.get("price")), billing_interval=interval, active=1 if f.get("active") == "1" else 0,
        )
    return _price_action("addon", key, save)


@bp.route("/pricing/code_fee/<key>", methods=["POST"])
@needs("platform.pricing.manage")
def price_code_fee_save(key):
    f = request.form
    return _price_action("code_fee", key, lambda: catalogue.update_code_fee(
        key, g.principal, name=" ".join((f.get("name") or "").split())[:80] or catalogue.code_fee(key)["name"],
        setup_fee=catalogue.parse_kes(f.get("setup_fee")), monthly_fee=catalogue.parse_kes(f.get("monthly_fee")),
        note=(f.get("note") or "").strip()[:200]))


# ---------------------------------------------------------------- USSD codes

@bp.route("/ussd-codes", methods=["GET"])
@needs("platform.ussd_codes.manage", "platform.tenants.view")
def codes_page():
    return render_template("platform/codes.html", page="codes", codes=ussd_codes.codes(), allocations=ussd_codes.allocations(),
                           code_types=catalogue.CODE_TYPES, fees={f["code_type"]: f for f in catalogue.code_fees()},
                           dial=ussd_codes.dial_string, manage=rbac.can(g.principal, "platform.ussd_codes.manage"))


@bp.route("/ussd-codes", methods=["POST"])
@needs("platform.ussd_codes.manage")
def codes_add():
    f = request.form
    try:
        ussd_codes.add_code(g.principal, f.get("code"), f.get("code_type"), f.get("provider") or "", f.get("note") or "",
                            catalogue.parse_kes(f.get("monthly_cost"), allow_empty=True))
    except ValueError as e:
        return failed(str(e), "platform.codes_page")
    return done("Code added to the list. Assign it from an organisation's page.", "platform.codes_page")


@bp.route("/ussd-codes/<int:code_id>/<action>", methods=["POST"])
@needs("platform.ussd_codes.manage")
def codes_action(code_id, action):
    actions = {"main": (ussd_codes.set_main, "Main code changed."), "retire": (ussd_codes.retire, "Code retired.")}
    if action not in actions:
        abort(404)
    fn, message = actions[action]
    try:
        fn(g.principal, code_id)
    except ValueError as e:
        return failed(str(e), "platform.codes_page")
    return done(message, "platform.codes_page")


# ---------------------------------------------------------------- staff

@bp.route("/staff", methods=["GET"])
@needs("platform.staff.manage")
def staff_page():
    rows = [dict(s, roles=rbac.staff_roles(s["id"])) for s in staff_auth.all_staff()]
    return render_template("platform/staff.html", page="staff", rows=rows, roles=rbac.platform_roles(),
                           role_names={k: v[0] for k, v in SYSTEM_ROLES.items()})


@bp.route("/staff", methods=["POST"])
@needs("platform.staff.manage")
def staff_create():
    f = request.form
    try:
        staff_auth.create_staff(g.principal, f.get("email"), f.get("name"), f.get("password"), f.getlist("roles"))
    except (ValueError, rbac.PermissionDenied) as e:
        return failed(str(e), "platform.staff_page")
    return done("Staff account created.", "platform.staff_page")


@bp.route("/staff/<int:staff_id>", methods=["POST"])
@needs("platform.staff.manage")
def staff_update(staff_id):
    if not staff_auth.staff(staff_id):
        abort(404)
    f = request.form
    try:
        if f.get("action") == "role":
            role, on = f.get("role"), f.get("on") == "1"
            if on:
                rbac.grant_platform(g.principal, staff_id, role)
            else:
                if g.staff and g.staff["id"] == staff_id and role == "platform_super_admin":
                    raise ValueError("You can't remove your own Super Admin role.")
                rbac.revoke_platform(g.principal, staff_id, role)
        elif f.get("action") == "active":
            if g.staff and g.staff["id"] == staff_id:
                raise ValueError("You can't deactivate yourself.")
            staff_auth.set_active(g.principal, staff_id, f.get("on") == "1")
        elif f.get("action") == "password":
            staff_auth.set_password(g.principal, staff_id, f.get("password"))
        else:
            abort(400)
    except (ValueError, rbac.PermissionDenied) as e:
        return failed(str(e), "platform.staff_page")
    return done("Saved.", "platform.staff_page")


# ---------------------------------------------------------------- people (everyone's accounts)

def _phone_from_url(digits):
    from pb.users import get_user
    phone = "+" + digits if digits.isdigit() else None
    if not phone or not get_user(phone, fresh=True):
        abort(404)
    return phone


@bp.route("/people", methods=["GET"])
@needs("platform.users.view", "platform.users.manage")
def people_page():
    from pb import accounts_admin
    kind = request.args.get("kind") if request.args.get("kind") in accounts_admin.KINDS else "all"
    q = (request.args.get("q") or "").strip()
    page_no = int(request.args.get("page") or 1) if str(request.args.get("page") or "1").isdigit() else 1
    rows, total = accounts_admin.search(q, kind, page_no)
    return render_template("platform/people.html", page="people", rows=rows, total=total, q=q, kind=kind,
                           kinds=accounts_admin.KINDS, page_no=page_no, pages=max(1, -(-total // accounts_admin.PAGE)))


@bp.route("/people/<digits>", methods=["GET"])
@needs("platform.users.view", "platform.users.manage")
def person_page(digits):
    from pb import accounts_admin
    from pb.ussd.session import NETWORKS
    phone = _phone_from_url(digits)
    return render_template("platform/person.html", page="people", p=accounts_admin.detail(phone), digits=digits,
                           networks=NETWORKS, manage=rbac.can(g.principal, "platform.users.manage"))


@bp.route("/people/<digits>", methods=["POST"])
@needs("platform.users.manage")
def person_update(digits):
    from pb import accounts_admin
    phone = _phone_from_url(digits)
    action = request.form.get("action")
    try:
        if action == "off":
            accounts_admin.turn(g.principal, phone, True)
            message = "Account turned off: it can't log in anywhere until you turn it back on. Its data is kept."
        elif action == "on":
            accounts_admin.turn(g.principal, phone, False)
            message = "Account turned on."
        elif action == "logout":
            accounts_admin.log_out_everywhere(g.principal, phone)
            message = "Logged out everywhere: the app, the website and USSD."
        elif action == "unlock":
            accounts_admin.unlock(g.principal, phone)
            message = "Unlocked: wrong PIN and password counts are cleared."
        elif action == "rename":
            accounts_admin.rename(g.principal, phone, request.form.get("name"))
            message = "Name saved."
        else:
            abort(400)
    except ValueError as e:
        return failed(str(e), "platform.person_page", digits=digits)
    return done(message, "platform.person_page", digits=digits)


# ---------------------------------------------------------------- test accounts (made by scripts/seed_test_accounts.py)

def _test_users():
    from pb.db import fetch_all
    users = fetch_all("SELECT phone, name, disabled FROM users WHERE is_test = 1 ORDER BY name, phone")
    return [dict(u, orgs=[o["name"] for o in tenancy.memberships_of(u["phone"])], local="0" + u["phone"][4:] if u["phone"].startswith("+254") else u["phone"])
            for u in users]


@bp.route("/test-accounts", methods=["GET"])
@needs("platform.flags.manage")
def test_accounts_page():
    from pb.db import fetch_all
    users = _test_users()
    staff_rows = fetch_all("SELECT id, email, name, active FROM staff_users WHERE is_test = 1 ORDER BY name")
    org_names = sorted({o for u in users for o in u["orgs"]}, key=str.lower)
    return render_template("platform/test_accounts.html", page="test_accounts", users=users, staff_rows=staff_rows,
                           org_names=org_names, off=sum(1 for u in users if u["disabled"]) + sum(1 for s in staff_rows if not s["active"]),
                           me=g.staff["id"] if g.staff else None)


@bp.route("/test-accounts", methods=["POST"])
@needs("platform.flags.manage")
def test_accounts_update():
    from pb.db import fetch_all
    from pb.users import set_disabled
    f, me = request.form, g.staff["id"] if g.staff else None
    action = f.get("action")
    try:
        if action in ("user_on", "user_off"):
            row = fetch_one("SELECT phone FROM users WHERE phone = ? AND is_test = 1", (f.get("phone"),))
            if not row:
                raise ValueError("That isn't a test account.")
            set_disabled(g.principal, row["phone"], action == "user_off")
            message = "Account turned off: it can't log in until you turn it back on." if action == "user_off" else "Account turned on."
        elif action in ("staff_on", "staff_off"):
            row = fetch_one("SELECT id FROM staff_users WHERE id = ? AND is_test = 1", (int(f.get("staff_id") or 0),))
            if not row:
                raise ValueError("That isn't a test staff account.")
            if row["id"] == me and action == "staff_off":
                raise ValueError("You can't turn off the account you're logged in with.")
            staff_auth.set_active(g.principal, row["id"], action == "staff_on")
            message = "Staff account turned off." if action == "staff_off" else "Staff account turned on."
        elif action in ("all_on", "all_off"):
            off = action == "all_off"
            org = f.get("org") or ""
            count = 0
            for u in _test_users():
                if (not org or org in u["orgs"] or (org == "-" and not u["orgs"])) and bool(u["disabled"]) != off:
                    set_disabled(g.principal, u["phone"], off)
                    count += 1
            if not org:
                for s in fetch_all("SELECT id, active FROM staff_users WHERE is_test = 1"):
                    if s["id"] != me and bool(s["active"]) == off:
                        staff_auth.set_active(g.principal, s["id"], not off)
                        count += 1
            message = f"{count} test account{'' if count == 1 else 's'} turned {'off' if off else 'on'}."
        else:
            abort(400)
    except ValueError as e:
        return failed(str(e), "platform.test_accounts_page")
    return done(message, "platform.test_accounts_page")


# ---------------------------------------------------------------- settings (platform switches, editions)

@bp.route("/settings", methods=["GET"])
@needs("platform.flags.manage")
def settings_page():
    return render_template("platform/settings.html", page="settings", otp=feature_on("otp", fresh=True),
                           sms_ready=sms is not None, editions=tenancy.editions(),
                           support=support.support_details(), support_fields=support.SUPPORT_FIELDS, ai=support.ai_enabled())


@bp.route("/settings", methods=["POST"])
@needs("platform.flags.manage")
def settings_save():
    f = request.form
    if f.get("action") == "otp":
        on = f.get("on") == "1"
        if on and sms is None:
            return failed("OTP needs SMS: set AT_API_KEY on the server first.", "platform.settings_page")
        set_feature("otp", on)
        audit("flag.otp", None, ("flag", "otp"), {"on": on}, actor=g.principal)
        return done(f"OTP {'on' if on else 'off'}.", "platform.settings_page")
    if f.get("action") == "support":
        old = support.support_details()
        support.save_support_details(f)
        audit("support.details", None, ("settings", "support"),
              {k: [old.get(k), (f.get(k) or "").strip()] for k in support.SUPPORT_FIELDS if old.get(k) != (f.get(k) or "").strip()},
              actor=g.principal)
        return done("Support details saved. They show on the help page (/help).", "platform.settings_page")
    if f.get("action") == "edition":
        key = f.get("edition")
        if not tenancy.edition(key):
            abort(400)
        tenancy.set_edition_active(g.principal, key, f.get("on") == "1")
        return done("Edition updated.", "platform.settings_page")
    abort(400)


# ---------------------------------------------------------------- audit log

@bp.route("/audit", methods=["GET"])
@needs("platform.audit.view")
def audit_page():
    tenant = request.args.get("tenant", "").strip()
    rows = audit_entries(tenant_id=int(tenant) if tenant.isdigit() else None, actor=request.args.get("actor", "").strip() or None,
                         action=request.args.get("action", "").strip() or None)
    return render_template("platform/audit.html", page="audit", rows=rows, args=request.args, permission_names=ALL_PERMISSIONS)
