import re
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from flask import has_app_context

from pb.config import DATABASE_URL, EMAIL_FROM, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USER, log
from pb.db import execute, fetch_all, fetch_one, get_db, sql
from pb.features import SETTINGS_TTL, feature_on
from pb.text import local_number
from pb.users import emails_json, get_user, user_emails
from pb.web import app


# ---------------------------------------------------------------- email (optional)
# Verification codes and security notifications by email. Both are off until the admin turns
# them on at /admin, and both need SMTP_* settings, so the prototype never depends on email.

USSD_CODE_TEXT = "*384*88041#"
EMAIL_CODE_TTL = 15 * 60


def email_configured():
    return bool(SMTP_HOST and EMAIL_FROM)


def send_email(to, subject, body):
    """Send one email now; raises on failure (the admin test uses this to show the error)."""
    msg = EmailMessage()
    if has_app_context():
        sender, signature = email_part("sender_name"), email_part("signature")
    else:  # background thread: read the admin's settings in a context of its own
        with app.app_context():
            sender, signature = email_part("sender_name"), email_part("signature")
    sender = sender.replace('"', "").replace("<", "").replace(">", "")[:60]
    msg["From"] = f'"{sender}" <{EMAIL_FROM}>'
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body + (f"\n\n{signature}" if signature.strip() else ""))
    context = ssl.create_default_context()
    if SMTP_PORT == 465:
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15, context=context)
    else:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
        server.starttls(context=context)
    with server:
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)


def send_email_async(to, subject, body):
    """Email in the background, so USSD screens and app requests never wait on the mail server."""
    def _send():
        try:
            send_email(to, subject, body)
            log.info("email sent to %s: %s", to, subject)
        except Exception:
            log.exception("email to %s failed", to)

    threading.Thread(target=_send, daemon=True).start()


NOTIFICATION_DAYS = 90  # the in-app inbox keeps this long


def notification_emails(owner):
    """The account's emails that get notifications: switched on by the user (and verified, while
    the admin has email verification on)."""
    if not email_configured():
        return []
    verified_only = feature_on("email_verification")
    return [r["email"] for r in user_emails(owner) if r["notify"] and (r["verified"] or not verified_only)]


# ---------------------------------------------------------------- message templates
# The admin edits every notification and email at /admin/templates: on/off, subject and text with
# {placeholders}. Edits are stored in the settings table ("tpl:<key>:subject" ...); empty = default.

COMMON_VARS = {"name": "the user's name", "phone": "their phone number", "app": "Remote Phonebook", "ussd_code": USSD_CODE_TEXT}
TEMPLATES = {
    "new_login": {
        "label": "New app login", "kind": "security", "vars": {"device": "the phone or browser"},
        "subject": "New login to your {app} account",
        "body": "Hi {name},\n\nYour account was just logged into from {device}.\n\nIf this wasn't you, change your password in the app.",
    },
    "password_changed": {
        "label": "Password changed", "kind": "security", "vars": {},
        "subject": "Your password was changed",
        "body": "Hi {name},\n\nYour {app} password was just changed, and your other devices were logged out.\n\n"
                "If this wasn't you, reset your password with Forgot password in the app.",
    },
    "password_reset": {
        "label": "Password reset", "kind": "security", "vars": {},
        "subject": "Your password was reset",
        "body": "Hi {name},\n\nYour {app} password was just reset with a code sent to your phone.\n\n"
                "If this wasn't you, reset it again and contact support.",
    },
    "pin_changed": {
        "label": "USSD PIN changed", "kind": "security", "vars": {},
        "subject": "Your USSD PIN was changed",
        "body": "Hi {name},\n\nThe PIN for {ussd_code} on your {app} account was just changed.\n\n"
                "If this wasn't you, change your password and PIN in the app.",
    },
    "ussd_other_phone": {
        "label": "USSD login from another phone", "kind": "security", "vars": {"caller": "the phone that dialled"},
        "subject": "USSD login from another phone",
        "body": "Hi {name},\n\nYour {app} account was opened on USSD from {caller}.\n\n"
                "If this wasn't you, end the session in the app (Sessions) and change your USSD PIN.",
    },
    "pin_lockout": {
        "label": "USSD locked after wrong PINs", "kind": "security", "vars": {"attempts": "wrong PINs", "minutes": "lockout"},
        "subject": "USSD login locked after wrong PINs",
        "body": "Hi {name},\n\n{attempts} wrong PINs were entered for your account on USSD, so USSD login is locked "
                "for {minutes} minutes.\n\nIf this wasn't you, change your USSD PIN in the app.",
    },
    "report_reply": {
        "label": "Reply to a report", "kind": "messages",
        "vars": {"report_id": "report number", "status": "resolved / answered", "report": "what they wrote", "reply": "your reply"},
        "subject": "Your report #{report_id} was {status}",
        "body": "Hi {name},\n\nYou wrote:\n{report}\n\nOur reply:\n{reply}\n\nYou can also see it in the app under Report a problem.",
    },
    "email_code": {
        "label": "Email verification code (email only)", "kind": "email", "vars": {"code": "the 6-digit code", "minutes": "valid for", "email": "the address"},
        "subject": "Your {app} verification code",
        "body": "Your code is {code}. Enter it in the {app} app to verify {email}. It expires in {minutes} minutes.",
    },
}
EMAIL_PARTS = {  # editable parts of every email
    "sender_name": ("Sender name", "Remote Phonebook"),
    "notify_footer": ("Footer on notification emails", "Choose where you get notifications in the app: Notifications."),
    "signature": ("Signature on every email", "- Remote Phonebook (Oduma Corp)"),
}
SAMPLE_VARS = {
    "name": "Jane", "phone": "0712345678", "device": "Pixel 8", "caller": "0712***678", "attempts": "5", "minutes": "15",
    "report_id": "12", "status": "resolved", "report": "I can't add a contact", "reply": "Fixed, please try again.",
    "code": "123456", "email": "jane@example.com",
}
_text_cache = {}


def setting_text(key):
    """A stored setting ("" when not set), cached briefly like the feature switches."""
    cached = _text_cache.get(key)
    if cached and time.time() - cached[1] < SETTINGS_TTL:
        return cached[0]
    row = fetch_one("SELECT value FROM settings WHERE key = ?", (key,))
    value = row["value"] if row else ""
    _text_cache[key] = (value, time.time())
    return value


def set_setting_text(key, value):
    if value:
        execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    else:
        execute("DELETE FROM settings WHERE key = ?", (key,))
    _text_cache[key] = (value, time.time())


def template_part(key, part):
    """The admin's version of a template's subject/body, or the default."""
    return setting_text(f"tpl:{key}:{part}") or TEMPLATES[key][part]


def template_on(key):
    return setting_text(f"tpl:{key}:enabled") != "0"


NONE = "__none__"  # stored when the admin empties an email part on purpose


def email_part(key):
    value = setting_text(f"email:{key}")
    return "" if value == NONE else value or EMAIL_PARTS[key][1]


def fill(text, values):
    """Replace {placeholders} we know; anything else in braces is left as typed."""
    return re.sub(r"\{(\w+)\}", lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), text)


def render_message(key, values):
    values = {"app": "Remote Phonebook", "ussd_code": USSD_CODE_TEXT, **values}
    return fill(template_part(key, "subject"), values), fill(template_part(key, "body"), values)


def notify_event(owner, key, complaint_id=None, **values):
    """Send one of the admin-editable notifications to an account."""
    try:
        user = get_user(owner)
        if not user:
            return []
        values = {"name": user["name"] or "there", "phone": local_number(owner), **values}
        subject, body = render_message(key, values)
        log_kind = "reply" if key == "report_reply" else "security" if TEMPLATES[key]["kind"] == "security" else "message"
        if not template_on(key):
            log_notification(owner, owner, log_kind, subject, body, note="Not sent: this notification is turned off in admin",
                             complaint_id=complaint_id)
            return []
        return notify(owner, subject, body, kind=TEMPLATES[key]["kind"], log_kind=log_kind, complaint_id=complaint_id)
    except Exception:
        log.exception("notification %s for %s failed", key, owner)
        return []


NOTIFICATION_LOG_DAYS = 90
LOG_KINDS = {"security": "Security alert", "message": "Message from the team", "reply": "Reply to a report"}


def insert_id(query, params):
    """INSERT and return the new row's id (Postgres RETURNING / SQLite lastrowid)."""
    db = get_db()
    cur = db.cursor()
    cur.execute(sql(query + (" RETURNING id" if DATABASE_URL else "")), params)
    new_id = cur.fetchone()["id"] if DATABASE_URL else cur.lastrowid
    db.commit()
    return new_id


def log_notification(owner, recipient, kind, title, body, inapp_id=None, emails=(), note=None, complaint_id=None):
    now = int(time.time())
    log_id = insert_id(
        "INSERT INTO notification_log (owner, recipient, kind, title, body, inapp_id, emails, email_status, note, "
        "complaint_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (owner, recipient, kind, title[:200], body[:2000], inapp_id, ", ".join(emails) or None,
         "sending" if emails else "none", note, complaint_id, now),
    )
    execute("DELETE FROM notification_log WHERE created_at < ?", (now - NOTIFICATION_LOG_DAYS * 86400,))
    return log_id


def notify(owner, subject, body, kind="security", log_kind=None, complaint_id=None):
    """Tell the account holder something, where they chose to hear it.

    kind "security" (logins, password/PIN changes, lockouts) or "messages" (replies to reports and
    messages from the team). Each goes to the in-app inbox and the user's chosen emails, unless the
    user turned that kind off. Security emails also need the admin's "Email notifications" switch;
    messages from the team are emailed whenever email is configured. Every one is logged for the
    admin (log_kind: security / message / reply). Returns the emails it went to.
    """
    log_kind = log_kind or ("security" if kind == "security" else "message")
    try:
        user = get_user(owner)
        if not user:
            return []
        if not user["notify_security" if kind == "security" else "notify_messages"]:
            what = "security alerts" if kind == "security" else "messages from the team"
            log_notification(owner, owner, log_kind, subject, body, note=f"Not delivered: the user turned off {what}",
                             complaint_id=complaint_id)
            return []
        now, inapp_id, notes = int(time.time()), None, []
        if user["notify_inapp"]:
            inapp_id = insert_id(
                "INSERT INTO notifications (owner, kind, title, body, created_at) VALUES (?, ?, ?, ?, ?)",
                (owner, kind, subject[:200], body[:2000], now),
            )
            execute("DELETE FROM notifications WHERE owner = ? AND created_at < ?", (owner, now - NOTIFICATION_DAYS * 86400))
        else:
            notes.append("in-app list turned off by the user")
        emails = []
        if kind == "security" and not feature_on("email_notifications"):
            notes.append("security emails are switched off in admin")
        elif not email_configured():
            notes.append("email isn't configured")
        else:
            emails = notification_emails(owner)
            if not emails:
                notes.append("no email chosen" + (" or verified" if feature_on("email_verification") else ""))
        footer = email_part("notify_footer")
        log_id = log_notification(owner, owner, log_kind, subject, body, inapp_id, emails,
                                  "; ".join(notes).capitalize() or None, complaint_id)
        send_emails_async(
            [(to, subject, body + (f"\n\n{footer}" if footer.strip() else "")) for to in emails], log_id
        )
        return emails
    except Exception:
        log.exception("notification for %s failed", owner)  # never break a login over a notification
        return []


def send_emails_async(messages, log_id=None):
    """Several emails, one after another in one background thread; the outcome goes in the admin log."""
    if not messages:
        return

    def _send():
        failures = []
        for to, subject, body in messages:
            try:
                send_email(to, subject, body)
                log.info("email sent to %s: %s", to, subject)
            except Exception as e:
                log.exception("email to %s failed", to)
                failures.append(f"{to}: {type(e).__name__}: {e}"[:300])
        if log_id is None:
            return
        status = "sent" if not failures else "failed" if len(failures) == len(messages) else "partial"
        try:
            with app.app_context():
                execute(
                    "UPDATE notification_log SET email_status = ?, note = COALESCE(note || '; ', '') || ? WHERE id = ?"
                    if failures else "UPDATE notification_log SET email_status = ? WHERE id = ?",
                    (status, "; ".join(failures), log_id) if failures else (status, log_id),
                )
        except Exception:
            log.exception("could not record email status")

    threading.Thread(target=_send, daemon=True).start()


def notifications_json(owner):
    rows = fetch_all(
        "SELECT id, kind, title, body, created_at, read_at FROM notifications WHERE owner = ? "
        "ORDER BY created_at DESC, id DESC LIMIT 100",
        (owner,),
    )
    return [
        {"id": r["id"], "kind": r["kind"], "title": r["title"], "body": r["body"], "created_at": r["created_at"],
         "read": r["read_at"] is not None}
        for r in rows
    ]


def unread_count(owner):
    return fetch_one("SELECT COUNT(*) AS n FROM notifications WHERE owner = ? AND read_at IS NULL", (owner,))["n"]


def notification_prefs(owner):
    user = get_user(owner, fresh=True)
    return {
        "inapp": bool(user["notify_inapp"]),
        "security": bool(user["notify_security"]),
        "messages": bool(user["notify_messages"]),
        "emails": emails_json(owner),
        "email_available": email_configured(),
        "verified_only": feature_on("email_verification"),
    }
