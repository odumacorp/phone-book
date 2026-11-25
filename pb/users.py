import time
from flask import g
from werkzeug.security import generate_password_hash

from pb.db import execute, fetch_all, fetch_one
from pb.text import EMAIL_RE


USER_COLUMNS = (
    "phone, pin_hash, failed_attempts, locked_until, show_others, name, "
    "email, password_hash, pw_failed_attempts, pw_locked_until, token_epoch, email_verified, "
    "notify_inapp, notify_security, notify_messages, sms_all, disabled, is_test"
)
DISABLED_MESSAGE = "This account is turned off. Contact Oduma Corp support."


def get_user(phone, fresh=False):
    """Users are cached for the rest of the request; writes that matter pass fresh=True."""
    cache = g.setdefault("users", {})
    if fresh or phone not in cache:
        cache[phone] = fetch_one(
            f"SELECT {USER_COLUMNS} FROM users WHERE phone = ?",
            (phone,),
        )
    return cache[phone]


def set_pin(phone, pin):
    """A PIN can be set only once; returns False if this number already has one."""
    if get_user(phone, fresh=True):
        return False
    execute("INSERT INTO users (phone, pin_hash) VALUES (?, ?)", (phone, generate_password_hash(pin)))
    get_user(phone, fresh=True)
    return True


def update_pin(phone, pin):
    """Change or reset an existing PIN (app only). Clears any lockout; old app sessions stop working."""
    execute(
        "UPDATE users SET pin_hash = ?, failed_attempts = 0, locked_until = 0 WHERE phone = ?",
        (generate_password_hash(pin), phone),
    )
    execute("DELETE FROM ussd_resume WHERE owner = ? OR resume_owner = ?", (phone, phone))  # end active USSD logins
    execute(
        "UPDATE ussd_sessions SET ended_at = ?, end_reason = 'PIN changed' WHERE owner = ? AND ended_at IS NULL",
        (int(time.time()), phone),
    )
    return get_user(phone, fresh=True)


def clean_email(raw):
    """Lower-cased email, "" when none was given, or None when it isn't a valid address."""
    email = str(raw or "").strip().lower()
    if not email:
        return ""
    return email if len(email) <= 254 and EMAIL_RE.match(email) else None


MAX_EMAILS = 3  # emails per account; each can be used to log in


def email_owner(email):
    row = fetch_one("SELECT owner FROM user_emails WHERE email = ?", (email,))
    return row["owner"] if row else None


def user_emails(owner):
    return fetch_all("SELECT id, email, verified, notify FROM user_emails WHERE owner = ? ORDER BY created_at, id", (owner,))


def emails_json(owner):
    return [
        {"id": r["id"], "email": r["email"], "verified": bool(r["verified"]), "notify": bool(r["notify"])}
        for r in user_emails(owner)
    ]


def set_disabled(actor, phone, disabled):
    """Turn an account off (it can't log in anywhere and its open sessions end) or back on. Data is kept."""
    from pb.audit import audit
    if not get_user(phone, fresh=True):
        raise ValueError("No account with that number.")
    now = int(time.time())
    if disabled:
        execute("UPDATE users SET disabled = 1, token_epoch = token_epoch + 1 WHERE phone = ?", (phone,))
        execute("UPDATE app_sessions SET ended_at = ?, end_reason = 'account turned off' WHERE owner = ? AND ended_at IS NULL",
                (now, phone))
        execute("DELETE FROM ussd_resume WHERE owner = ? OR resume_owner = ?", (phone, phone))
        execute("UPDATE ussd_sessions SET ended_at = ?, end_reason = 'account turned off' WHERE owner = ? AND ended_at IS NULL",
                (now, phone))
    else:
        execute("UPDATE users SET disabled = 0 WHERE phone = ?", (phone,))
    audit("user.disabled" if disabled else "user.enabled", None, ("user", phone), None, actor=actor)
    return get_user(phone, fresh=True)
