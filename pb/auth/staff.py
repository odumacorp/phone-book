"""Oduma Corp staff accounts for the platform portal (/platform): email + password, with lockout.

The /admin password (ADMIN_PASSWORD) still works as a break-glass Super Admin ("staff:root"), which is how the
first staff accounts are created. Staff get their permissions from platform roles (pb/rbac).
"""
import re
import time

from werkzeug.security import check_password_hash, generate_password_hash

from pb.audit import audit
from pb.config import LOCKOUT_SECONDS, MAX_PIN_ATTEMPTS
from pb.db import execute, fetch_all, fetch_one, insert_id
from pb.rbac import engine as rbac

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 10


def staff(staff_id):
    return fetch_one("SELECT id, email, name, active, last_login, created_at FROM staff_users WHERE id = ?", (staff_id,))


def all_staff():
    return fetch_all("SELECT id, email, name, active, last_login, created_at FROM staff_users ORDER BY name")


def create_staff(actor, email, name, password, role_keys=()):
    email = str(email or "").strip().lower()
    name = " ".join(str(name or "").split())[:80]
    if not EMAIL_RE.match(email):
        raise ValueError("Enter a valid email.")
    if not name:
        raise ValueError("Enter a name.")
    if len(password or "") < MIN_PASSWORD:
        raise ValueError(f"Passwords need at least {MIN_PASSWORD} characters.")
    if fetch_one("SELECT 1 AS x FROM staff_users WHERE email = ?", (email,)):
        raise ValueError("A staff account with this email already exists.")
    staff_id = insert_id(
        "INSERT INTO staff_users (email, name, password_hash, active, created_by, created_at) VALUES (?, ?, ?, 1, ?, ?)",
        (email, name, generate_password_hash(password), actor, int(time.time())),
    )
    audit("staff.create", None, ("staff", staff_id), {"email": email, "name": name}, actor=actor)
    for key in role_keys:
        rbac.grant_platform(actor, staff_id, key)
    return staff_id


def set_active(actor, staff_id, active):
    execute("UPDATE staff_users SET active = ? WHERE id = ?", (1 if active else 0, staff_id))
    audit("staff.set_active", None, ("staff", staff_id), {"active": bool(active)}, actor=actor)


def set_password(actor, staff_id, password):
    if len(password or "") < MIN_PASSWORD:
        raise ValueError(f"Passwords need at least {MIN_PASSWORD} characters.")
    execute("UPDATE staff_users SET password_hash = ?, failed_attempts = 0, locked_until = 0 WHERE id = ?",
            (generate_password_hash(password), staff_id))
    audit("staff.password", None, ("staff", staff_id), None, actor=actor)


def check_login(email, password):
    """Returns (staff row, None) or (None, error message). Locks for 15 minutes after 5 wrong passwords."""
    email = str(email or "").strip().lower()
    row = fetch_one(
        "SELECT id, email, name, password_hash, active, failed_attempts, locked_until FROM staff_users WHERE email = ?", (email,)
    )
    now = int(time.time())
    if not row or not row["active"]:
        return None, "Wrong email or password."
    if row["locked_until"] > now:
        return None, "Too many wrong passwords. Try again in 15 minutes."
    if not check_password_hash(row["password_hash"], password or ""):
        attempts = row["failed_attempts"] + 1
        locked = now + LOCKOUT_SECONDS if attempts >= MAX_PIN_ATTEMPTS else 0
        execute("UPDATE staff_users SET failed_attempts = ?, locked_until = ? WHERE id = ?", (0 if locked else attempts, locked, row["id"]))
        audit("staff.login_failed", None, ("staff", row["id"]), {"locked": bool(locked)}, actor=f"staff:{row['id']}")
        return None, "Too many wrong passwords. Try again in 15 minutes." if locked else "Wrong email or password."
    execute("UPDATE staff_users SET failed_attempts = 0, locked_until = 0, last_login = ? WHERE id = ?", (now, row["id"]))
    audit("staff.login", None, ("staff", row["id"]), None, actor=f"staff:{row['id']}")
    return row, None
