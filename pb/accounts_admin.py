"""The platform's People pages: Oduma Corp staff see and manage people's accounts (individuals and organisation members).

Staff see account facts (name, emails, organisations, logins, sessions) but never a person's own contacts: only how
many they have.
"""
import time

from pb.audit import audit
from pb.db import execute, fetch_all, fetch_one
from pb.org.content import profile_organisations
from pb.photos import photo_url
from pb.tenancy import tenants as tenancy
from pb.text import local_number
from pb.users import emails_json, get_user, set_disabled

KINDS = {"all": "Everyone", "individual": "Individuals (no organisation)", "org": "In organisations",
         "off": "Turned off", "test": "Test accounts"}
PAGE = 50


def search(query="", kind="all", page=1):
    """(rows, total) for the People list. Rows: phone, local, name, emails, orgs (count), disabled, is_test."""
    q = " ".join(str(query or "").split()).lower()
    rows = fetch_all("SELECT phone, name, disabled, is_test FROM users ORDER BY name, phone")
    emails = {}
    for r in fetch_all("SELECT owner, email FROM user_emails ORDER BY id"):
        emails.setdefault(r["owner"], []).append(r["email"])
    in_orgs = tenancy.phones_in_organisations()
    out = []
    digits = "".join(ch for ch in q if ch.isdigit())
    for r in rows:
        phone, local = r["phone"], local_number(r["phone"])
        if kind == "individual" and phone in in_orgs or kind == "org" and phone not in in_orgs:
            continue
        if kind == "off" and not r["disabled"] or kind == "test" and not r["is_test"]:
            continue
        mails = emails.get(phone, [])
        if q and not (q in (r["name"] or "").lower() or any(q in m.lower() for m in mails)
                      or (digits and (digits in phone or digits in local))):
            continue
        out.append({"phone": phone, "local": local, "name": r["name"], "emails": mails, "in_orgs": phone in in_orgs,
                    "disabled": bool(r["disabled"]), "is_test": bool(r["is_test"])})
    page = max(1, int(page or 1))
    return out[(page - 1) * PAGE: page * PAGE], len(out)


def detail(phone):
    user = get_user(phone, fresh=True)
    if not user:
        return None
    now = int(time.time())
    first = fetch_one("SELECT MIN(t) AS t FROM (SELECT MIN(created_at) AS t FROM app_sessions WHERE owner = ? "
                      "UNION ALL SELECT MIN(created_at) AS t FROM login_logs WHERE owner = ?) x", (phone, phone))
    return {
        "phone": phone, "local": local_number(phone), "name": user["name"], "photo": photo_url(phone),
        "emails": emails_json(phone), "disabled": bool(user["disabled"]), "is_test": bool(user["is_test"]),
        "has_password": bool(user["password_hash"]),
        "locked": user["locked_until"] > now or user["pw_locked_until"] > now
                  or bool(user["failed_attempts"]) or bool(user["pw_failed_attempts"]),
        "organisations": profile_organisations(phone),
        "contacts": fetch_one("SELECT COUNT(*) AS n FROM contacts WHERE owner = ?", (phone,))["n"],
        "first_seen": first["t"] if first else None,
        "sessions": fetch_all("SELECT device, created_at, last_seen FROM app_sessions WHERE owner = ? AND ended_at IS NULL "
                              "ORDER BY last_seen DESC LIMIT 20", (phone,)),
        "logins": fetch_all("SELECT created_at, caller, network, success FROM login_logs WHERE owner = ? "
                            "ORDER BY created_at DESC LIMIT 15", (phone,)),
    }


def log_out_everywhere(actor, phone):
    now = int(time.time())
    execute("UPDATE users SET token_epoch = token_epoch + 1 WHERE phone = ?", (phone,))
    execute("UPDATE app_sessions SET ended_at = ?, end_reason = 'logged out by Oduma Corp' WHERE owner = ? AND ended_at IS NULL",
            (now, phone))
    execute("DELETE FROM ussd_resume WHERE owner = ? OR resume_owner = ?", (phone, phone))
    execute("UPDATE ussd_sessions SET ended_at = ?, end_reason = 'logged out by Oduma Corp' WHERE owner = ? AND ended_at IS NULL",
            (now, phone))
    audit("user.logout_everywhere", None, ("user", phone), None, actor=actor)


def unlock(actor, phone):
    execute("UPDATE users SET failed_attempts = 0, locked_until = 0, pw_failed_attempts = 0, pw_locked_until = 0 WHERE phone = ?",
            (phone,))
    audit("user.unlock", None, ("user", phone), None, actor=actor)


def rename(actor, phone, name):
    name = " ".join(str(name or "").split())[:80]
    if not name:
        raise ValueError("Enter a name.")
    execute("UPDATE users SET name = ? WHERE phone = ?", (name, phone))
    audit("user.rename", None, ("user", phone), {"name": name}, actor=actor)


def turn(actor, phone, off):
    set_disabled(actor, phone, off)
