"""One-time codes by SMS, a second factor. Built but OFF until the Super Admin turns on the "otp" switch.

- App/website login: when OTP is on, a correct password returns a ticket instead of a token; the 6-digit code
  texted to the account's phone exchanges the ticket for the token (/api/login/otp).
- Sensitive USSD views (results, finance...): pb.tenancy.tenants.otp_on_for(tenant) decides, per organisation.

Codes are hashed, expire after 5 minutes, allow 5 tries, are single-use, and are rate-limited per phone.
"""
import secrets
import time

from werkzeug.security import check_password_hash, generate_password_hash

from pb.db import execute, fetch_one, insert_id
from pb.features import feature_on
from pb.messaging import send_sms_async
from pb import messaging

OTP_TTL = 5 * 60
OTP_RESEND_SECONDS = 60
OTP_MAX_PER_HOUR = 5
OTP_MAX_ATTEMPTS = 5


def otp_on():
    return feature_on("otp")


def login_needs_otp(phone):
    """App/website login: the platform switch, or any organisation the person belongs to that forces OTP on."""
    if otp_on():
        return True
    row = fetch_one(
        "SELECT 1 AS x FROM memberships m JOIN tenant_settings s ON s.tenant_id = m.tenant_id AND s.key = 'otp' "
        "WHERE m.user_phone = ? AND m.status = 'active' AND s.value = 'on'",
        (phone,),
    )
    return bool(row)


def send_otp(phone, purpose):
    """Text a code. Returns (ticket, None) or (None, error message)."""
    if messaging.sms is None:
        return None, "SMS service is not available right now."
    now = int(time.time())
    last = fetch_one("SELECT MAX(sent_at) AS t FROM otp_codes WHERE phone = ? AND purpose = ?", (phone, purpose))
    if last and last["t"] and now - last["t"] < OTP_RESEND_SECONDS:
        return None, "A code was just sent. Wait a minute before asking for another."
    hour = fetch_one("SELECT COUNT(*) AS n FROM otp_codes WHERE phone = ? AND sent_at > ?", (phone, now - 3600))
    if hour["n"] >= OTP_MAX_PER_HOUR:
        return None, "Too many codes requested. Try again in an hour."
    code = f"{secrets.randbelow(10 ** 6):06d}"
    ticket = secrets.token_urlsafe(24)
    execute("DELETE FROM otp_codes WHERE expires_at < ?", (now - 86400,))
    insert_id(
        "INSERT INTO otp_codes (phone, purpose, code_hash, ticket, expires_at, sent_at) VALUES (?, ?, ?, ?, ?, ?)",
        (phone, purpose, generate_password_hash(code), ticket, now + OTP_TTL, now),
    )
    send_sms_async(phone, f"Your Remote Phonebook code is {code}. It expires in 5 minutes. Never share it.")
    return ticket, None


def verify_otp(ticket, code, purpose):
    """Returns (phone, None) or (None, error message). A ticket works once."""
    row = fetch_one(
        "SELECT id, phone, purpose, code_hash, expires_at, attempts, used_at FROM otp_codes WHERE ticket = ?", (str(ticket or ""),)
    )
    now = int(time.time())
    if not row or row["purpose"] != purpose or row["used_at"]:
        return None, "This code is no longer valid. Log in again."
    if row["expires_at"] < now:
        return None, "This code has expired. Log in again."
    if row["attempts"] >= OTP_MAX_ATTEMPTS:
        return None, "Too many wrong codes. Log in again."
    if not check_password_hash(row["code_hash"], str(code or "").strip()):
        execute("UPDATE otp_codes SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
        left = OTP_MAX_ATTEMPTS - row["attempts"] - 1
        return None, f"Wrong code. {left} tries left." if left else "Too many wrong codes. Log in again."
    execute("UPDATE otp_codes SET used_at = ? WHERE id = ?", (now, row["id"]))
    return row["phone"], None
