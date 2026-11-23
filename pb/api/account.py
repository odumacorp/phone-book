import secrets
import time
from flask import g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from pb.api.auth import ALREADY_REGISTERED, EMAIL_TAKEN, NO_ACCOUNT, api_error, auth_status, body_json, check_password, find_account, logged_in, password_error, pin_error, require_auth, revoke_app_session, set_password, token_version
from pb.config import MAX_RESET_ATTEMPTS, RESET_CODE_TTL, RESET_RESEND_SECONDS, RESUME_SECONDS, SECRET_KEY, SESSION_HISTORY_DAYS, TOKEN_MAX_AGE
from pb.contacts import sms_all
from pb.db import execute, fetch_all, fetch_one, get_db, sql
from pb.features import feature_on
from pb.messaging import send_sms_async, sms
from pb.notify import email_configured, notify_event, unread_count
from pb.text import LOGIN_PHONE_FORMAT, clean_name, local_number, login_phone
from pb.users import MAX_EMAILS, clean_email, email_owner, emails_json, get_user, set_pin, update_pin
from pb.ussd.session import LOGIN_LOG_DAYS, NETWORKS, close_idle_ussd_logins, masked
from pb.web import app


def active_ussd_sessions(owner):
    since = int(time.time()) - RESUME_SECONDS
    return fetch_all(
        "SELECT caller, saved_at FROM ussd_resume WHERE (owner = ? OR resume_owner = ?) "
        "AND logged_out = 0 AND saved_at >= ? ORDER BY saved_at DESC",
        (owner, owner, since),
    )


def end_ussd_sessions(owner, caller=None, reason="logged out from the app"):
    """Log out USSD from the app: the open session ends on its next screen, and no PIN-free continue.

    All of the account's USSD sessions, or only the one on `caller`.
    """
    only = " AND caller = ?" if caller else ""
    extra = (caller,) if caller else ()
    execute(
        "UPDATE ussd_resume SET logged_out = 1, resume_owner = NULL, resume_from = NULL, pin_mac = NULL "
        f"WHERE (owner = ? OR resume_owner = ?) AND logged_out = 0{only}",
        (owner, owner) + extra,
    )
    execute(
        f"UPDATE ussd_sessions SET ended_at = ?, end_reason = ? WHERE owner = ? AND ended_at IS NULL{only}",
        (int(time.time()), reason, owner) + extra,
    )


@app.route("/api/ussd/sessions", methods=["GET"])
@require_auth
def api_ussd_sessions():
    """Logged-in USSD sessions on this account (active until RESUME_SECONDS without a screen)."""
    return jsonify({
        "sessions": [
            {
                "phone": local_number(r["caller"]),
                "own_phone": r["caller"] == g.phone,
                "last_active": r["saved_at"],
                "expires_at": r["saved_at"] + RESUME_SECONDS,
            }
            for r in active_ussd_sessions(g.phone)
        ],
        "timeout_seconds": RESUME_SECONDS,
    })


@app.route("/api/ussd/logout", methods=["POST"])
@require_auth
def api_ussd_logout():
    """End all of this account's USSD sessions."""
    close_idle_ussd_logins()
    count = len(active_ussd_sessions(g.phone))
    end_ussd_sessions(g.phone)
    return jsonify({"logged_out": count})


def session_times(started, ended, now):
    return {
        "logged_in": started,
        "logged_out": ended,
        "duration_seconds": (ended or now) - started,
        "active": ended is None,
    }


@app.route("/api/sessions", methods=["GET"])
@require_auth
def api_sessions():
    """App devices and USSD logins on this account: active ones, then ended ones from the last
    SESSION_HISTORY_DAYS, with login time, logout time and duration."""
    now = int(time.time())
    since = now - SESSION_HISTORY_DAYS * 86400
    version = token_version(get_user(g.phone))
    # Expired or out-of-date app sessions end lazily (their tokens already stopped working)
    execute(
        "UPDATE app_sessions SET ended_at = created_at + ?, end_reason = 'expired' WHERE owner = ? AND ended_at IS NULL AND created_at < ?",
        (TOKEN_MAX_AGE, g.phone, now - TOKEN_MAX_AGE),
    )
    execute(
        "UPDATE app_sessions SET ended_at = ?, end_reason = 'password or PIN changed' "
        "WHERE owner = ? AND ended_at IS NULL AND version <> ?",
        (now, g.phone, version),
    )
    close_idle_ussd_logins()
    app_rows = fetch_all(
        "SELECT jti, device, created_at, last_seen, ended_at, end_reason FROM app_sessions "
        "WHERE owner = ? AND (ended_at IS NULL OR ended_at >= ?) ORDER BY created_at DESC LIMIT 50",
        (g.phone, since),
    )
    ussd_rows = fetch_all(
        "SELECT id, caller, network, started_at, last_active, ended_at, end_reason FROM ussd_sessions "
        "WHERE owner = ? AND (ended_at IS NULL OR ended_at >= ?) ORDER BY started_at DESC LIMIT 50",
        (g.phone, since),
    )
    active_first = lambda item: not item["active"]
    return jsonify({
        "app": sorted(
            (
                {
                    "id": r["jti"],
                    "device": r["device"],
                    "current": r["jti"] == g.jti,
                    "last_active": r["last_seen"],
                    "end_reason": r["end_reason"],
                    **session_times(r["created_at"], r["ended_at"], now),
                }
                for r in app_rows
            ),
            key=active_first,
        ),
        "ussd": sorted(
            (
                {
                    "id": r["id"],
                    "phone": local_number(r["caller"]),
                    "own_phone": r["caller"] == g.phone,
                    "network": NETWORKS.get(r["network"] or "", r["network"] or "Unknown network"),
                    "last_active": r["last_active"],
                    "expires_at": None if r["ended_at"] else r["last_active"] + RESUME_SECONDS,
                    "end_reason": r["end_reason"],
                    **session_times(r["started_at"], r["ended_at"], now),
                }
                for r in ussd_rows
            ),
            key=active_first,
        ),
        "ussd_timeout_seconds": RESUME_SECONDS,
        "history_days": SESSION_HISTORY_DAYS,
    })


@app.route("/api/sessions/app/<session_id>/logout", methods=["POST"])
@require_auth
def api_logout_app_session(session_id):
    row = fetch_one("SELECT ended_at FROM app_sessions WHERE jti = ? AND owner = ?", (session_id, g.phone))
    if not row or row["ended_at"]:
        return api_error("That session has already ended.", 404)
    current = session_id == g.jti
    revoke_app_session(g.phone, session_id, "logged out" if current else "logged out from another device")
    return jsonify({"logged_out": session_id, "current": current})


@app.route("/api/sessions/ussd/<int:login_id>/logout", methods=["POST"])
@require_auth
def api_logout_ussd_session(login_id):
    close_idle_ussd_logins()
    row = fetch_one("SELECT caller, ended_at FROM ussd_sessions WHERE id = ? AND owner = ?", (login_id, g.phone))
    if not row or row["ended_at"]:
        return api_error("That USSD session has already ended.", 404)
    end_ussd_sessions(g.phone, row["caller"])
    return jsonify({"logged_out": login_id})


@app.route("/api/logout", methods=["POST"])
@require_auth
def api_logout():
    """Log out this device, or everywhere: every app login plus any active USSD login."""
    if body_json().get("everywhere"):
        execute("UPDATE users SET token_epoch = token_epoch + 1 WHERE phone = ?", (g.phone,))
        execute(
            "UPDATE app_sessions SET ended_at = ?, end_reason = 'logged out everywhere' WHERE owner = ? AND ended_at IS NULL",
            (int(time.time()), g.phone),
        )
        end_ussd_sessions(g.phone, reason="logged out everywhere")
        return jsonify({"logged_out": "everywhere"})
    if g.jti:
        revoke_app_session(g.phone, g.jti, "logged out")
    return jsonify({"logged_out": "this device"})


@app.route("/api/password/change", methods=["POST"])
@require_auth
def api_change_password():
    body = body_json()
    current, new = str(body.get("current_password", "")), str(body.get("new_password", ""))
    error = password_error(new, body.get("confirm_password", new))
    if error:
        return api_error(error, 400)
    error = check_password(get_user(g.phone), current)
    if error:
        return api_error("Current password is wrong." if error.startswith("Wrong") else error, auth_status(error))
    notify_event(g.phone, "password_changed")
    return logged_in(set_password(g.phone, new), same_session=True)


@app.route("/api/pin/change", methods=["POST"])
@require_auth
def api_change_pin():
    """New USSD PIN, confirmed with the app password (so a forgotten PIN can be replaced)."""
    body = body_json()
    new_pin = str(body.get("new_pin", ""))
    error = pin_error(new_pin, str(body.get("confirm_pin", new_pin)))
    if error:
        return api_error(error, 400)
    error = check_password(get_user(g.phone), str(body.get("password", "")))
    if error:
        return api_error("Password is wrong." if error.startswith("Wrong") else error, auth_status(error))
    notify_event(g.phone, "pin_changed")
    return logged_in(update_pin(g.phone, new_pin), same_session=True)


# SMS verification codes (stored in pin_resets) prove the user holds the SIM. They are used to
# register a new number and to reset a forgotten password.

def send_code(phone, purpose):
    """Text a fresh 6-digit code. Returns an error message, or None when sent."""
    now = int(time.time())
    row = fetch_one("SELECT sent_at FROM pin_resets WHERE phone = ?", (phone,))
    if row and now - row["sent_at"] < RESET_RESEND_SECONDS:
        return "A code was just sent. Wait a minute before asking for another."
    code = f"{secrets.randbelow(10 ** 6):06d}"
    db = get_db()
    db.execute(sql("DELETE FROM pin_resets WHERE phone = ?"), (phone,))
    db.execute(
        sql("INSERT INTO pin_resets (phone, code_hash, expires_at, sent_at) VALUES (?, ?, ?, ?)"),
        (phone, generate_password_hash(code), now + RESET_CODE_TTL, now),
    )
    db.commit()
    send_sms_async(phone, f"Your Remote Phone-Book {purpose} code is {code}. It expires in 10 minutes.")
    return None


def use_code(phone, code):
    """Check a code; it can be used once. Returns (error message, status) or None when valid."""
    row = fetch_one("SELECT code_hash, expires_at, attempts FROM pin_resets WHERE phone = ?", (phone,))
    if not row or row["expires_at"] < time.time():
        return "This code has expired. Ask for a new one.", 400
    if not check_password_hash(row["code_hash"], code):
        attempts = row["attempts"] + 1
        if attempts >= MAX_RESET_ATTEMPTS:
            execute("DELETE FROM pin_resets WHERE phone = ?", (phone,))
            return "Too many wrong codes. Ask for a new one.", 429
        execute("UPDATE pin_resets SET attempts = ? WHERE phone = ?", (attempts, phone))
        return "Wrong code.", 401
    execute("DELETE FROM pin_resets WHERE phone = ?", (phone,))
    return None


def registration_error(body, phone):
    """Everything about a registration that can be checked before the SMS code is used up."""
    if not phone:
        return f"Enter your phone number as {LOGIN_PHONE_FORMAT}.", 400
    if get_user(phone):
        return ALREADY_REGISTERED, 409
    email = clean_email(body.get("email"))
    if email is None:
        return "Enter a valid email address, or leave it empty.", 400
    if email and email_owner(email):
        return EMAIL_TAKEN, 409
    return None


@app.route("/api/register/request", methods=["POST"])
def api_register_request():
    """Register, step 1: text a 6-digit code to the new number."""
    body = body_json()
    phone = login_phone(str(body.get("phone", "")))
    failed = registration_error(body, phone)
    if failed:
        return api_error(*failed)
    if sms is None:
        return api_error("SMS service is not available right now.", 503)
    error = send_code(phone, "verification")
    if error:
        return api_error(error, 429)
    return jsonify({"sent": True, "phone": phone})


@app.route("/api/register/confirm", methods=["POST"])
def api_register_confirm():
    """Register, step 2: SMS code + name, optional email, app password and USSD PIN. Logs the user in."""
    if not SECRET_KEY:
        return api_error("App login is not configured on the server.", 503)
    body = body_json()
    phone = login_phone(str(body.get("phone", "")))
    failed = registration_error(body, phone)
    if failed:
        return api_error(*failed)
    name = clean_name(body.get("name"))
    password, pin = str(body.get("password", "")), str(body.get("pin", ""))
    error = (
        (None if name else "Enter your name.")
        or password_error(password, body.get("confirm_password", password))
        or pin_error(pin, str(body.get("confirm_pin", pin)))
    )
    if error:
        return api_error(error, 400)
    failed = use_code(phone, str(body.get("code", "")).strip())
    if failed:
        return api_error(*failed)
    if not set_pin(phone, pin):
        return api_error(ALREADY_REGISTERED, 409)
    execute("UPDATE users SET name = ?, password_hash = ? WHERE phone = ?", (name, generate_password_hash(password), phone))
    email = clean_email(body.get("email"))
    if email:
        execute("INSERT INTO user_emails (owner, email, created_at) VALUES (?, ?, ?)", (phone, email, int(time.time())))
    return logged_in(get_user(phone, fresh=True))


@app.route("/api/password/reset/request", methods=["POST"])
def api_reset_request():
    """Forgot password, step 1: text a code to the account's phone (found by email or phone)."""
    user = find_account(body_json().get("login"))
    if not user:
        return api_error(NO_ACCOUNT, 404)
    if sms is None:
        return api_error("SMS service is not available right now.", 503)
    error = send_code(user["phone"], "reset")
    if error:
        return api_error(error, 429)
    return jsonify({"sent": True, "phone": masked(user["phone"])})


@app.route("/api/password/reset/confirm", methods=["POST"])
def api_reset_confirm():
    """Forgot password, step 2: SMS code + new password. Clears the password lockout and logs in."""
    if not SECRET_KEY:
        return api_error("App login is not configured on the server.", 503)
    body = body_json()
    user = find_account(body.get("login"))
    if not user:
        return api_error(NO_ACCOUNT, 404)
    password = str(body.get("new_password", ""))
    error = password_error(password, body.get("confirm_password", password))
    if error:
        return api_error(error, 400)
    failed = use_code(user["phone"], str(body.get("code", "")).strip())
    if failed:
        return api_error(*failed)
    notify_event(user["phone"], "password_reset")
    return logged_in(set_password(user["phone"], password))


@app.route("/api/me", methods=["GET"])
@require_auth
def api_me():
    row = fetch_one("SELECT COUNT(*) AS n FROM contacts WHERE owner = ?", (g.phone,))
    user = get_user(g.phone)
    emails = emails_json(g.phone)
    return jsonify({
        "phone": g.phone,
        "contacts": row["n"],
        "show_others": bool(user["show_others"]),
        "sms_all": sms_all(user),
        "name": user["name"],
        "email": emails[0]["email"] if emails else None,  # the first one (older app versions)
        "emails": emails,
        "max_emails": MAX_EMAILS,
        "features": {"email_verification": feature_on("email_verification") and email_configured()},
        "unread_notifications": unread_count(g.phone),
    })


@app.route("/api/logins", methods=["GET"])
@require_auth
def api_logins():
    """USSD login history for this account, newest first."""
    try:
        limit = max(1, min(int(request.args.get("limit", 100)), 200))
    except ValueError:
        limit = 100
    rows = fetch_all(
        "SELECT caller, network, success, reason, created_at FROM login_logs WHERE owner = ? "
        "ORDER BY created_at DESC, id DESC LIMIT ?",
        (g.phone, limit),
    )
    return jsonify({
        "logins": [
            {
                "at": r["created_at"],
                "phone": local_number(r["caller"]),
                "own_phone": r["caller"] == g.phone,
                "network": NETWORKS.get(r["network"] or "", r["network"] or "Unknown network"),
                "success": bool(r["success"]),
                "reason": r["reason"],
            }
            for r in rows
        ],
        "keep_days": LOGIN_LOG_DAYS,
    })
