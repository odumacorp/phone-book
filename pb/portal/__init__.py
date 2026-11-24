"""The portal (/platform), for two kinds of people:

- Oduma Corp staff (email + password): organisations, prices, USSD codes, staff, settings, audit log.
  The /admin password also logs in, as a break-glass Super Admin ("staff:root").
- Organisation admins and operators (their app phone number or email + password): their organisation's workspace
  at /platform/org, where they add the contacts, emergency contacts and notices their members see on USSD and in the app.

Every page checks a permission; every POST needs the CSRF token. Pages are server-rendered like /admin.
"""
import hmac
import time
from functools import wraps

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for

from pb.admin import admin_enabled, csrf_token
from pb.api.auth import check_password, find_account, require_auth
from pb.auth import staff as staff_auth
from pb.auth.otp import login_needs_otp, send_otp, verify_otp
from pb.billing.catalogue import kes
from pb.config import SECRET_KEY
from pb.db import fetch_all, fetch_one
from pb.text import local_number
from pb.rbac import engine as rbac
from pb.users import get_user

bp = Blueprint("platform", __name__, url_prefix="/platform")
OPEN_ENDPOINTS = ("platform.login", "platform.login_post", "platform.login_code", "platform.app_login")


def portal_enabled():
    return bool(SECRET_KEY)


def current_principal():
    if session.get("staff_id"):
        row = staff_auth.staff(session["staff_id"])
        if row and row["active"]:
            return rbac.staff_principal(row["id"]), dict(row, kind="staff")
        session.pop("staff_id", None)
    if session.get("user_phone"):
        user = get_user(session["user_phone"])
        if user and user["password_hash"]:
            return rbac.user_principal(user["phone"]), {"id": None, "name": user["name"] or user["phone"], "email": "",
                                                        "phone": user["phone"], "kind": "org"}
        session.pop("user_phone", None)
    if session.get("admin") is True and admin_enabled():
        return rbac.ROOT, {"id": None, "name": "Admin (password)", "email": "", "kind": "staff"}
    return None, None


def is_staff():
    return bool(getattr(g, "staff", None)) and g.staff["kind"] == "staff"


@bp.before_request
def load_principal():
    if not portal_enabled():
        abort(404)
    g.channel = "portal"
    g.principal, g.staff = current_principal()
    if request.endpoint in OPEN_ENDPOINTS:
        return None
    if not g.principal:
        return redirect(url_for("platform.login"))
    token = request.form.get("csrf") or request.headers.get("X-CSRF-Token") or ""
    if request.method == "POST" and not hmac.compare_digest(str(token), session.get("csrf", "") or "x"):
        abort(403)
    return None


@bp.app_context_processor
def portal_helpers():
    def allowed(permission):
        return bool(getattr(g, "principal", None)) and rbac.can(g.principal, permission)

    def names(table):
        return {r["key"]: r["name"] for r in fetch_all(f"SELECT key, name FROM {table}")}

    return {
        "kes": kes,
        "local": local_number,
        "plan_name": lambda key: names("plans").get(key, key or "-"),
        "edition_name": lambda key: names("editions").get(key, key or "-"),
        "csrf": csrf_token(),
        "allowed": allowed,
        "staff_user": getattr(g, "staff", None),
        "is_staff": is_staff() if getattr(g, "principal", None) else False,
        "fmt_time": lambda t: time.strftime("%d %b %Y, %H:%M", time.gmtime(t + 3 * 3600)) if t else "-",
        "fmt_date": lambda t: time.strftime("%d %b %Y", time.gmtime(t + 3 * 3600)) if t else "-",
    }


def needs(*permissions):
    """The page needs any one of these platform permissions."""
    def wrap(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not any(rbac.can(g.principal, p) for p in permissions):
                abort(403)
            return view(*args, **kwargs)
        return wrapper
    return wrap


def done(message, endpoint, **values):
    flash(message, "ok")
    return redirect(url_for(endpoint, **values))


def failed(message, endpoint, **values):
    flash(message, "error")
    return redirect(url_for(endpoint, **values))


@bp.route("/login", methods=["GET"])
def login():
    if g.principal:
        return redirect(url_for("platform.dashboard"))
    pending = session.get("pending_login")
    return render_template("platform/login.html", error=request.args.get("error"),
                           code_step=bool(pending), phone_hint=pending.get("hint") if pending else None)


def _start_session(**values):
    session.clear()
    session.permanent = True
    session.update(values)


@bp.route("/login", methods=["POST"], endpoint="login_post")
def login_post():
    if not hmac.compare_digest(str(request.form.get("csrf", "")), session.get("csrf", "") or "x"):
        return redirect(url_for("platform.login", error="Your login form expired. Try again."))
    login_id, password = (request.form.get("login") or request.form.get("email") or "").strip(), request.form.get("password") or ""
    # Oduma Corp staff log in with their work email
    if "@" in login_id:
        if fetch_one("SELECT 1 AS x FROM staff_users WHERE email = ?", (login_id.lower(),)):
            row, error = staff_auth.check_login(login_id, password)
            if error:
                return redirect(url_for("platform.login", error=error))
            _start_session(staff_id=row["id"])
            return redirect(url_for("platform.dashboard"))
    # Organisation admins and operators: their Remote Phonebook account (phone number or email + password)
    user = find_account(login_id)
    if not user:
        return redirect(url_for("platform.login", error="Wrong email, phone number or password."))
    error = check_password(user, password)
    if error:
        return redirect(url_for("platform.login", error=error.replace("Wrong email/phone or password.", "Wrong email, phone number or password.")))
    if login_needs_otp(user["phone"]):
        ticket, error = send_otp(user["phone"], "portal_login")
        if error:
            return redirect(url_for("platform.login", error=error))
        csrf = session.get("csrf")
        session.clear()
        session["csrf"] = csrf
        hint = "0" + user["phone"][4:7] + "***" + user["phone"][-3:] if user["phone"].startswith("+254") else user["phone"]
        session["pending_login"] = {"ticket": ticket, "hint": hint}
        return redirect(url_for("platform.login"))
    _start_session(user_phone=user["phone"])
    return redirect(url_for("platform.dashboard"))


@bp.route("/login/code", methods=["POST"], endpoint="login_code")
def login_code():
    pending = session.get("pending_login")
    if not pending or not hmac.compare_digest(str(request.form.get("csrf", "")), session.get("csrf", "") or "x"):
        session.pop("pending_login", None)
        return redirect(url_for("platform.login", error="Your login expired. Start again."))
    if request.form.get("cancel"):
        session.pop("pending_login", None)
        return redirect(url_for("platform.login"))
    phone, error = verify_otp(pending["ticket"], request.form.get("code"), "portal_login")
    if error:
        if "Log in again" in error:
            session.pop("pending_login", None)
        return redirect(url_for("platform.login", error=error))
    _start_session(user_phone=phone)
    return redirect(url_for("platform.dashboard"))


@bp.route("/app-login", methods=["POST"], endpoint="app_login")
@require_auth
def app_login():
    """The website's "Manage organisation" button: turn its app login (bearer token, sent only by our own page,
    which other sites can't do) into a portal session, so the person isn't asked to log in again."""
    _start_session(user_phone=g.phone)
    from flask import jsonify
    return jsonify({"ok": True, "next": url_for("platform.dashboard")})


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("platform.login"))


@bp.cli.command("create-staff")
def create_staff_command():
    """Create a staff account from the shell: flask --app app platform create-staff"""
    import click

    email = click.prompt("Email")
    name = click.prompt("Name")
    password = click.prompt("Password (10+ characters)", hide_input=True, confirmation_prompt=True)
    role = click.prompt("Role", default="platform_super_admin",
                        type=click.Choice(["platform_super_admin", "platform_support", "platform_finance", "reseller"]))
    staff_id = staff_auth.create_staff("cli", email, name, password, [role] if role else [])
    click.echo(f"Created staff #{staff_id} ({email}) with role {role}. Log in at /platform.")


from pb.portal import views, org_views  # noqa: E402,F401  (register the pages on bp)
