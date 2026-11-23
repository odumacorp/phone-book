import secrets
import time
from flask import g, jsonify
from werkzeug.security import check_password_hash, generate_password_hash

from pb.api.auth import EMAIL_TAKEN, api_error, body_json, require_auth
from pb.config import MAX_RESET_ATTEMPTS, RESET_RESEND_SECONDS
from pb.db import execute, fetch_one, get_db, sql
from pb.features import feature_on
from pb.notify import EMAIL_CODE_TTL, NOTIFICATION_DAYS, email_configured, notification_prefs, notifications_json, render_message, send_email_async, unread_count
from pb.users import MAX_EMAILS, clean_email, email_owner, emails_json, user_emails
from pb.web import app


@app.route("/api/notifications", methods=["GET"])
@require_auth
def api_notifications():
    """The in-app inbox (newest first) and where the user gets notifications."""
    return jsonify({
        "notifications": notifications_json(g.phone),
        "unread": unread_count(g.phone),
        "prefs": notification_prefs(g.phone),
        "keep_days": NOTIFICATION_DAYS,
    })


@app.route("/api/notifications/read", methods=["POST"])
@require_auth
def api_notifications_read():
    execute("UPDATE notifications SET read_at = ? WHERE owner = ? AND read_at IS NULL", (int(time.time()), g.phone))
    return jsonify({"unread": 0})


@app.route("/api/notifications/clear", methods=["POST"])
@require_auth
def api_notifications_clear():
    execute("DELETE FROM notifications WHERE owner = ?", (g.phone,))
    return jsonify({"cleared": True})


@app.route("/api/notifications/prefs", methods=["POST"])
@require_auth
def api_notification_prefs():
    """{inapp?, security?, messages?: bool} and/or {email_id, notify: bool} for one email.
    {all: false} stops every notification (all kinds, inbox and emails); {all: true} turns them back on."""
    body = body_json()
    flags = {"inapp": "notify_inapp", "security": "notify_security", "messages": "notify_messages"}
    if "all" in body:
        if not isinstance(body["all"], bool):
            return api_error("Expected all: true or false.", 400)
        on = int(body["all"])
        execute("UPDATE users SET notify_inapp = ?, notify_security = ?, notify_messages = ? WHERE phone = ?", (on, on, on, g.phone))
        execute("UPDATE user_emails SET notify = ? WHERE owner = ?", (on, g.phone))
    for key, column in flags.items():
        if key in body:
            if not isinstance(body[key], bool):
                return api_error(f"Expected {key}: true or false.", 400)
            execute(f"UPDATE users SET {column} = ? WHERE phone = ?", (int(body[key]), g.phone))
    if "email_id" in body:
        if not isinstance(body.get("notify"), bool):
            return api_error("Expected notify: true or false.", 400)
        if not fetch_one("SELECT 1 AS x FROM user_emails WHERE id = ? AND owner = ?", (body["email_id"], g.phone)):
            return api_error("Email not found.", 404)
        execute("UPDATE user_emails SET notify = ? WHERE id = ? AND owner = ?", (int(body["notify"]), body["email_id"], g.phone))
    return jsonify(notification_prefs(g.phone))


@app.route("/api/emails", methods=["GET"])
@require_auth
def api_emails():
    return jsonify({"emails": emails_json(g.phone), "max_emails": MAX_EMAILS})


@app.route("/api/emails", methods=["POST"])
@require_auth
def api_add_email():
    """Add an email (up to MAX_EMAILS). Any of them can be used to log in."""
    email = clean_email(body_json().get("email"))
    if not email:
        return api_error("Enter a valid email address.", 400)
    owner = email_owner(email)
    if owner == g.phone:
        return api_error("You've already added this email.", 409)
    if owner:
        return api_error(EMAIL_TAKEN, 409)
    if len(user_emails(g.phone)) >= MAX_EMAILS:
        return api_error(f"You can add up to {MAX_EMAILS} emails. Remove one first.", 409)
    execute("INSERT INTO user_emails (owner, email, created_at) VALUES (?, ?, ?)", (g.phone, email, int(time.time())))
    row = fetch_one("SELECT id, email, verified FROM user_emails WHERE email = ?", (email,))
    return jsonify({"id": row["id"], "email": row["email"], "verified": False}), 201


@app.route("/api/emails/<int:email_id>", methods=["DELETE"])
@require_auth
def api_delete_email(email_id):
    row = fetch_one("SELECT email FROM user_emails WHERE id = ? AND owner = ?", (email_id, g.phone))
    if not row:
        return api_error("Email not found.", 404)
    execute("DELETE FROM user_emails WHERE id = ? AND owner = ?", (email_id, g.phone))
    execute("DELETE FROM email_codes WHERE phone = ? AND email = ?", (g.phone, row["email"]))
    return jsonify({"deleted": email_id})


def email_to_verify(email_id):
    """(the user_emails row, None) or (None, (error, status))."""
    if not (feature_on("email_verification") and email_configured()):
        return None, ("Email verification is not available right now.", 503)
    row = fetch_one("SELECT id, email, verified FROM user_emails WHERE id = ? AND owner = ?", (email_id, g.phone))
    if not row:
        return None, ("Email not found.", 404)
    if row["verified"]:
        return None, ("This email is already verified.", 409)
    return row, None


@app.route("/api/email/verify/request", methods=["POST"])
@require_auth
def api_email_verify_request():
    """Email a 6-digit code to one of the account's addresses ({id})."""
    row, failed = email_to_verify(body_json().get("id"))
    if failed:
        return api_error(*failed)
    now = int(time.time())
    pending = fetch_one("SELECT sent_at FROM email_codes WHERE phone = ?", (g.phone,))
    if pending and now - pending["sent_at"] < RESET_RESEND_SECONDS:
        return api_error("A code was just sent. Wait a minute before asking for another.", 429)
    code = f"{secrets.randbelow(10 ** 6):06d}"
    db = get_db()
    db.execute(sql("DELETE FROM email_codes WHERE phone = ?"), (g.phone,))
    db.execute(
        sql("INSERT INTO email_codes (phone, email, code_hash, expires_at, sent_at) VALUES (?, ?, ?, ?, ?)"),
        (g.phone, row["email"], generate_password_hash(code), now + EMAIL_CODE_TTL, now),
    )
    db.commit()
    subject, text = render_message("email_code", {"code": code, "minutes": EMAIL_CODE_TTL // 60, "email": row["email"]})
    send_email_async(row["email"], subject, text)
    return jsonify({"sent": True, "email": row["email"]})


@app.route("/api/email/verify/confirm", methods=["POST"])
@require_auth
def api_email_verify_confirm():
    body = body_json()
    row, failed = email_to_verify(body.get("id"))
    if failed:
        return api_error(*failed)
    code = str(body.get("code", "")).strip()
    pending = fetch_one("SELECT email, code_hash, expires_at, attempts FROM email_codes WHERE phone = ?", (g.phone,))
    if not pending or pending["expires_at"] < time.time() or pending["email"] != row["email"]:
        return api_error("This code has expired. Ask for a new one.", 400)
    if not check_password_hash(pending["code_hash"], code):
        if pending["attempts"] + 1 >= MAX_RESET_ATTEMPTS:
            execute("DELETE FROM email_codes WHERE phone = ?", (g.phone,))
            return api_error("Too many wrong codes. Ask for a new one.", 429)
        execute("UPDATE email_codes SET attempts = attempts + 1 WHERE phone = ?", (g.phone,))
        return api_error("Wrong code.", 401)
    execute("DELETE FROM email_codes WHERE phone = ?", (g.phone,))
    execute("UPDATE user_emails SET verified = 1 WHERE id = ?", (row["id"],))
    return jsonify({"id": row["id"], "email": row["email"], "verified": True})
