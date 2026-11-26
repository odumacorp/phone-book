import time
from flask import g

from pb.config import CALL_TTL, PAGE_SIZE
from pb.crypto import open_contacts, stored_contact
from pb.db import execute, fetch_all, fetch_one, get_db, sql
from pb.users import get_user


def contact_order(r):
    """In the order the person arranged them; the ones they haven't arranged after those, priority first then A-Z."""
    arranged = r.get("sort")
    return (arranged is None, arranged or 0, -r["priority"], r["name"].lower(), r["id"])


def list_contacts(owner, priority_only=False):
    """Decrypted contacts in the person's order (sorted here: the database only holds ciphertext)."""
    only = " AND priority = 1" if priority_only else ""
    rows = open_contacts(fetch_all(f"SELECT id, name, phone, priority, enc, sort FROM contacts WHERE owner = ?{only}", (owner,)))
    return sorted(rows, key=contact_order)


def set_order(owner, contact_ids):
    """Save the order the person arranged (their own contacts only; anything else is ignored). Returns how many."""
    mine = {r["id"] for r in fetch_all("SELECT id FROM contacts WHERE owner = ?", (owner,))}
    wanted = [int(i) for i in contact_ids if str(i).lstrip("-").isdigit() and int(i) in mine]
    db = get_db()
    for position, contact_id in enumerate(dict.fromkeys(wanted), 1):
        db.execute(sql("UPDATE contacts SET sort = ? WHERE id = ? AND owner = ?"), (position, contact_id, owner))
    db.commit()
    from pb.analytics import track_user
    track_user("contacts_arranged", owner, count=len(wanted))
    return len(wanted)


def search_contacts(owner, query, priority_only=False):
    query = query.lower()
    return [r for r in list_contacts(owner, priority_only) if query in r["name"].lower()][:PAGE_SIZE]


def priority_only(owner):
    """USSD shows only priority contacts when the user turned off "show other contacts" in the app."""
    return not get_user(owner)["show_others"]


def sms_all(user):
    """Whether "SMS me my contacts" sends all contacts (True) or only priority ones (False)."""
    return bool(user["show_others"] if user["sms_all"] is None else user["sms_all"])


def add_contact(owner, name, phone, channel=None):
    from pb.analytics import track_user
    track_user("contact_added", owner, channel=channel)
    g.action_done = True
    execute("INSERT INTO contacts (owner, name, phone, enc) VALUES (?, ?, ?, ?)", (owner, *stored_contact(name, phone)))


def delete_contact(owner, contact_id):
    g.action_done = True
    execute("DELETE FROM contacts WHERE id = ? AND owner = ?", (contact_id, owner))


def find_contact_by_phone(owner, phone):
    rows = open_contacts(fetch_all("SELECT id, name, phone, enc FROM contacts WHERE owner = ? ORDER BY id", (owner,)))
    return next((r for r in rows if r["phone"] == phone), None)


def save_pending_call(user_phone, contact_phone):
    db = get_db()
    db.execute(sql("DELETE FROM pending_calls WHERE user_phone = ?"), (user_phone,))
    db.execute(
        sql("INSERT INTO pending_calls (user_phone, contact_phone, created_at) VALUES (?, ?, ?)"),
        (user_phone, contact_phone, int(time.time())),
    )
    db.commit()


def pop_pending_call(user_phone):
    row = fetch_one("SELECT contact_phone, created_at FROM pending_calls WHERE user_phone = ?", (user_phone,))
    if not row:
        return None
    execute("DELETE FROM pending_calls WHERE user_phone = ?", (user_phone,))
    return row["contact_phone"] if time.time() - row["created_at"] <= CALL_TTL else None
