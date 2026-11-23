"""The platform portal (/platform) for Oduma Corp staff: organisations, prices, USSD codes, staff, settings, audit.

Login: a staff account (email + password), or the /admin password as a break-glass Super Admin ("staff:root").
Every page checks a platform permission; every POST needs the CSRF token. Pages are server-rendered like /admin.
"""
import hmac
import time
from functools import wraps

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for

from pb.admin import admin_enabled, csrf_token
from pb.auth import staff as staff_auth
from pb.billing.catalogue import kes
from pb.config import SECRET_KEY
from pb.rbac import engine as rbac

bp = Blueprint("platform", __name__, url_prefix="/platform")


def portal_enabled():
    return bool(SECRET_KEY)


def current_principal():
    if session.get("staff_id"):
        row = staff_auth.staff(session["staff_id"])
        if row and row["active"]:
            return rbac.staff_principal(row["id"]), row
        session.pop("staff_id", None)
    if session.get("admin") is True and admin_enabled():
        return rbac.ROOT, {"id": None, "name": "Admin (password)", "email": ""}
    return None, None


@bp.before_request
def load_principal():
    if not portal_enabled():
        abort(404)
    g.channel = "portal"
    g.principal, g.staff = current_principal()
    if request.endpoint in ("platform.login", "platform.login_post"):
        return None
    if not g.principal:
        return redirect(url_for("platform.login"))
    if request.method == "POST" and not hmac.compare_digest(str(request.form.get("csrf", "")), session.get("csrf", "") or "x"):
        abort(403)
    return None


@bp.app_context_processor
def portal_helpers():
    def allowed(permission):
        return bool(getattr(g, "principal", None)) and rbac.can(g.principal, permission)

    return {
        "kes": kes,
        "csrf": csrf_token(),
        "allowed": allowed,
        "staff_user": getattr(g, "staff", None),
        "fmt_time": lambda t: time.strftime("%d %b %Y %H:%M", time.gmtime(t + 3 * 3600)) + " EAT" if t else "-",
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
    return render_template("platform/login.html", error=request.args.get("error"))


@bp.route("/login", methods=["POST"], endpoint="login_post")
def login_post():
    if not hmac.compare_digest(str(request.form.get("csrf", "")), session.get("csrf", "") or "x"):
        return redirect(url_for("platform.login", error="Your login form expired. Try again."))
    row, error = staff_auth.check_login(request.form.get("email"), request.form.get("password"))
    if error:
        return redirect(url_for("platform.login", error=error))
    session.clear()
    session.permanent = True
    session["staff_id"] = row["id"]
    return redirect(url_for("platform.dashboard"))


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("platform.login"))


from pb.portal import views  # noqa: E402,F401  (registers the pages on bp)


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
