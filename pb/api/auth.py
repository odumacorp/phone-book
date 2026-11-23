import hashlib
import secrets
import time
from flask import g, jsonify, request
from functools import wraps
from itsdangerous import BadSignature, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from pb.config import LOCKOUT_SECONDS, MAX_PIN_ATTEMPTS, SECRET_KEY, TOKEN_MAX_AGE
from pb.db import execute, fetch_one, get_db, sql
from pb.features import feature_on
from pb.notify import notify_event
from pb.text import LOGIN_PHONE_FORMAT, PIN_RE, login_phone
from pb.users import clean_email, email_owner, get_user
from pb.web import app


# ---------------------------------------------------------------- mobile app API
# The app logs in with email or phone + a password the user chooses. USSD keeps phone + 4-digit PIN
# (letters are slow on a keypad); both are set when registering in the app.


PASSWORD_MIN, PASSWORD_MAX = 8, 128
NO_ACCOUNT = "No account found for this email or phone number. Tap Register to create one."
NO_PIN_YET = "This number is not registered yet. Tap Register to create your account."
ALREADY_REGISTERED = "This number is already registered. Log in, or use Forgot password."
EMAIL_TAKEN = "This email is already used by another account."


def serializer():
    return URLSafeTimedSerializer(SECRET_KEY, salt="phonebook-app")


def token_version(user):
    # Changes when the password or PIN changes, which logs out other app sessions
    # ...and on "log out everywhere" (token_epoch)
    secret = f"{user['pin_hash']}|{user['password_hash'] or ''}|{user['token_epoch']}".encode()
    return hashlib.sha256(secret).hexdigest()[:16]


def make_token(user, keep_session=None):
    """A new app login. Its jti identifies the session so it can be listed and logged out on its own.

    keep_session: the jti of this device's session when its password/PIN just changed, so it
    carries on as the same session instead of showing up as a new login.
    """
    version, now = token_version(user), int(time.time())
    kept = keep_session and fetch_one(
        "SELECT 1 AS x FROM app_sessions WHERE jti = ? AND owner = ? AND ended_at IS NULL", (keep_session, user["phone"])
    )
    if kept:
        jti = keep_session
        execute("UPDATE app_sessions SET version = ?, last_seen = ? WHERE jti = ?", (version, now, jti))
    else:
        jti = secrets.token_urlsafe(12)
        device = " ".join(request.headers.get("X-Device-Name", "").split())[:60] or "Unknown device"
        execute(
            "INSERT INTO app_sessions (jti, owner, device, version, created_at, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
            (jti, user["phone"], device, version, now, now),
        )
    # A changed password or PIN logs out the other devices: record when and why
    execute(
        "UPDATE app_sessions SET ended_at = ?, end_reason = 'password or PIN changed' "
        "WHERE owner = ? AND ended_at IS NULL AND version <> ?",
        (now, user["phone"], version),
    )
    return serializer().dumps({"phone": user["phone"], "v": version, "jti": jti})


def revoke_app_session(owner, jti, reason):
    now = int(time.time())
    execute(
        "UPDATE app_sessions SET ended_at = ?, end_reason = ? WHERE jti = ? AND owner = ? AND ended_at IS NULL",
        (now, reason, jti, owner),
    )
    db = get_db()
    db.execute(sql("DELETE FROM revoked_tokens WHERE expires_at < ?"), (now,))
    db.execute(
        sql("INSERT INTO revoked_tokens (jti, expires_at) VALUES (?, ?) ON CONFLICT (jti) DO NOTHING"),
        (jti, now + TOKEN_MAX_AGE),
    )
    db.commit()


def api_error(message, status):
    return jsonify({"error": message}), status


def require_auth(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not SECRET_KEY:
            return api_error("App login is not configured on the server.", 503)
        header = request.headers.get("Authorization", "")
        token = header[7:] if header.startswith("Bearer ") else ""
        try:
            data = serializer().loads(token, max_age=TOKEN_MAX_AGE)
        except BadSignature:
            return api_error("Please log in again.", 401)
        user = get_user(data.get("phone", ""))
        if not user or token_version(user) != data.get("v"):
            return api_error("Please log in again.", 401)
        jti = data.get("jti")
        if jti and fetch_one("SELECT 1 AS x FROM revoked_tokens WHERE jti = ?", (jti,)):
            return api_error("Please log in again.", 401)
        if jti:
            now = int(time.time())  # "last active", written at most once a minute
            execute("UPDATE app_sessions SET last_seen = ? WHERE jti = ? AND last_seen < ?", (now, jti, now - 60))
        g.jti = jti
        g.phone = user["phone"]
        return view(*args, **kwargs)

    return wrapper


def find_account(login):
    """The user for an email address or a phone number (+254... / 0...), or None."""
    login = str(login or "").strip()
    if "@" in login:
        email = clean_email(login)
        phone = email_owner(email) if email else None
    else:
        phone = login_phone(login)
    return get_user(phone) if phone else None


def password_error(password, confirm):
    if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        return f"Password must be {PASSWORD_MIN} to {PASSWORD_MAX} characters."
    if confirm is not None and confirm != password:
        return "Passwords do not match."
    return None


def pin_error(new_pin, confirm):
    if not PIN_RE.match(new_pin):
        return "PIN must be exactly 4 digits."
    if confirm is not None and confirm != new_pin:
        return "PINs do not match."
    return None


def check_password(user, password):
    """Like check_pin, with its own lockout so app guesses can't lock USSD (and vice versa)."""
    phone = user["phone"]
    if not user["password_hash"]:
        return "Set a password first: tap Forgot password."
    now = int(time.time())
    if user["pw_locked_until"] > now:
        minutes = (user["pw_locked_until"] - now + 59) // 60
        return f"Too many wrong passwords. Try again in {minutes} min, or use Forgot password."
    if not check_password_hash(user["password_hash"], password):
        attempts = user["pw_failed_attempts"] + 1
        locked_until = now + LOCKOUT_SECONDS if attempts >= MAX_PIN_ATTEMPTS else 0
        execute(
            "UPDATE users SET pw_failed_attempts = ?, pw_locked_until = ? WHERE phone = ?",
            (0 if locked_until else attempts, locked_until, phone),
        )
        if locked_until:
            return "Too many wrong passwords. Try again in 15 min, or use Forgot password."
        return "Wrong email/phone or password."
    if user["pw_failed_attempts"]:
        execute("UPDATE users SET pw_failed_attempts = 0 WHERE phone = ?", (phone,))
    return None


def auth_status(error):
    return 429 if error.startswith("Too many") else 401


def set_password(phone, password):
    execute(
        "UPDATE users SET password_hash = ?, pw_failed_attempts = 0, pw_locked_until = 0 WHERE phone = ?",
        (generate_password_hash(password), phone),
    )
    return get_user(phone, fresh=True)


def logged_in(user, same_session=False):
    keep = getattr(g, "jti", None) if same_session else None
    return jsonify({"token": make_token(user, keep), "phone": user["phone"]})


def body_json():
    return request.get_json(silent=True) or {}


@app.route("/api/login", methods=["POST"])
def api_login():
    if not SECRET_KEY:
        return api_error("App login is not configured on the server.", 503)
    body = body_json()
    login, password = str(body.get("login", "")).strip(), str(body.get("password", ""))
    if not login or not password:
        return api_error("Enter your email or phone number and your password.", 400)
    if "@" not in login and not login_phone(login):
        return api_error(f"Enter a valid email, or a phone number as {LOGIN_PHONE_FORMAT}.", 400)
    user = find_account(login)
    if not user:
        return api_error(NO_ACCOUNT, 401)
    email_row = fetch_one("SELECT verified FROM user_emails WHERE email = ?", (clean_email(login),)) if "@" in login else None
    if email_row and feature_on("email_verification") and not email_row["verified"]:
        return api_error("Verify your email in the app before logging in with it. Use your phone number for now.", 401)
    error = check_password(user, password)
    if error:
        return api_error(error, auth_status(error))
    device = " ".join(request.headers.get("X-Device-Name", "").split())[:60] or "an unknown device"
    notify_event(user["phone"], "new_login", device=device)
    return logged_in(user)
