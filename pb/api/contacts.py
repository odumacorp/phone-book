from flask import g, jsonify, request

from pb.api.auth import api_error, body_json, require_auth
from pb.config import MAX_PRIORITY, MAX_SYNC_CONTACTS
from pb.contacts import add_contact, delete_contact, find_contact_by_phone, list_contacts, sms_all
from pb.crypto import encrypting, stored_contact
from pb.db import execute, fetch_all, fetch_one, get_db, sql
from pb.text import EMERGENCY_NUMBERS, clean_name, normalize_contact_phone
from pb.users import get_user
from pb.org.managed_contacts import blocked_message
from pb.web import app


@app.route("/api/contacts", methods=["GET"])
@require_auth
def api_contacts():
    rows = list_contacts(g.phone)
    return jsonify({
        "contacts": [
            {"id": r["id"], "name": r["name"], "phone": r["phone"], "priority": bool(r["priority"])} for r in rows
        ],
        "max_priority": MAX_PRIORITY,
        "show_others": bool(get_user(g.phone)["show_others"]),
        "sms_all": sms_all(get_user(g.phone)),
    })


@app.route("/api/emergency", methods=["GET"])
@require_auth
def api_emergency():
    saved = {r["phone"] for r in list_contacts(g.phone)}
    return jsonify({"numbers": [{"number": n, "label": label, "saved": n in saved} for n, label, _short in EMERGENCY_NUMBERS]})


@app.route("/api/contacts", methods=["POST"])
@require_auth
def api_add_contact():
    blocked = blocked_message(g.phone)
    if blocked:
        return api_error(blocked, 403)
    body = body_json()
    name = " ".join(str(body.get("name") or "").split())[:60]
    phone = normalize_contact_phone(str(body.get("phone") or ""))
    if not name:
        return api_error("Enter a name.", 400)
    if not phone:
        return api_error("Enter a valid phone number.", 400)
    existing = find_contact_by_phone(g.phone, phone)
    if existing:
        return api_error(f"{phone} is already saved as {existing['name']}.", 409)
    add_contact(g.phone, name, phone)
    row = find_contact_by_phone(g.phone, phone)
    return jsonify({"id": row["id"], "name": row["name"], "phone": row["phone"], "priority": False}), 201


@app.route("/api/contacts/<int:contact_id>", methods=["DELETE"])
@require_auth
def api_delete_contact(contact_id):
    blocked = blocked_message(g.phone)
    if blocked:
        return api_error(blocked, 403)
    if not fetch_one("SELECT id FROM contacts WHERE id = ? AND owner = ?", (contact_id, g.phone)):
        return api_error("Contact not found.", 404)
    delete_contact(g.phone, contact_id)
    return jsonify({"deleted": contact_id})


@app.route("/api/contacts/delete", methods=["POST"])
@require_auth
def api_delete_contacts():
    blocked = blocked_message(g.phone)
    if blocked:
        return api_error(blocked, 403)
    """Bulk delete: {ids: [...]}. Ids that are not the user's are ignored."""
    ids = body_json().get("ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        return api_error("Expected ids: a list of contact ids.", 400)
    if len(ids) > 500:
        return api_error("Delete at most 500 contacts at a time.", 400)
    ids = list(dict.fromkeys(ids))
    marks = ", ".join("?" * len(ids))
    owned = [r["id"] for r in fetch_all(f"SELECT id FROM contacts WHERE owner = ? AND id IN ({marks})", (g.phone, *ids))]
    if owned:
        marks = ", ".join("?" * len(owned))
        execute(f"DELETE FROM contacts WHERE owner = ? AND id IN ({marks})", (g.phone, *owned))
    return jsonify({"deleted": owned})


@app.route("/api/contacts/<int:contact_id>", methods=["PATCH"])
@require_auth
def api_edit_contact(contact_id):
    blocked = blocked_message(g.phone)
    if blocked:
        return api_error(blocked, 403)
    contact = fetch_one("SELECT priority FROM contacts WHERE id = ? AND owner = ?", (contact_id, g.phone))
    if not contact:
        return api_error("Contact not found.", 404)
    body = body_json()
    name = " ".join(str(body.get("name") or "").split())[:60]
    phone = normalize_contact_phone(str(body.get("phone") or ""))
    if not name:
        return api_error("Enter a name.", 400)
    if not phone:
        return api_error("Enter a valid phone number.", 400)
    existing = find_contact_by_phone(g.phone, phone)
    if existing and existing["id"] != contact_id:
        return api_error(f"{phone} is already saved as {existing['name']}.", 409)
    execute(
        "UPDATE contacts SET name = ?, phone = ?, enc = ? WHERE id = ? AND owner = ?",
        (*stored_contact(name, phone), contact_id, g.phone),
    )
    return jsonify({"id": contact_id, "name": name, "phone": phone, "priority": bool(contact["priority"])})


@app.route("/api/contacts/<int:contact_id>/priority", methods=["POST"])
@require_auth
def api_set_priority(contact_id):
    want = bool((request.get_json(silent=True) or {}).get("priority"))
    contact = fetch_one("SELECT priority FROM contacts WHERE id = ? AND owner = ?", (contact_id, g.phone))
    if not contact:
        return api_error("Contact not found.", 404)
    if want and not contact["priority"]:
        row = fetch_one("SELECT COUNT(*) AS n FROM contacts WHERE owner = ? AND priority = 1", (g.phone,))
        if row["n"] >= MAX_PRIORITY:
            return api_error(f"You can have up to {MAX_PRIORITY} priority contacts. Remove one first.", 409)
    execute("UPDATE contacts SET priority = ? WHERE id = ? AND owner = ?", (int(want), contact_id, g.phone))
    return jsonify({"id": contact_id, "priority": want})


@app.route("/api/settings", methods=["POST"])
@require_auth
def api_settings():
    """Update show_others, sms_all and/or name (signs the user's SMS). Emails: /api/emails."""
    body = request.get_json(silent=True) or {}
    if not {"show_others", "sms_all", "name"} & set(body):
        return api_error("Nothing to update." + (" Emails are managed with /api/emails." if "email" in body else ""), 400)
    if "show_others" in body:
        if not isinstance(body["show_others"], bool):
            return api_error("Expected show_others: true or false.", 400)
        execute("UPDATE users SET show_others = ? WHERE phone = ?", (int(body["show_others"]), g.phone))
    if "sms_all" in body:
        if not isinstance(body["sms_all"], bool):
            return api_error("Expected sms_all: true or false.", 400)
        execute("UPDATE users SET sms_all = ? WHERE phone = ?", (int(body["sms_all"]), g.phone))
    if "name" in body:
        name = clean_name(body["name"])
        if not name:
            return api_error("Enter your name.", 400)
        execute("UPDATE users SET name = ? WHERE phone = ?", (name, g.phone))
    user = get_user(g.phone, fresh=True)
    return jsonify({"show_others": bool(user["show_others"]), "sms_all": sms_all(user), "name": user["name"]})


@app.route("/api/contacts/sync", methods=["POST"])
@require_auth
def api_sync():
    blocked = blocked_message(g.phone)
    if blocked:
        return api_error(blocked, 403)
    body = request.get_json(silent=True) or {}
    incoming = body.get("contacts")
    if not isinstance(incoming, list):
        return api_error("Expected a list of contacts.", 400)
    if len(incoming) > MAX_SYNC_CONTACTS:
        return api_error(f"At most {MAX_SYNC_CONTACTS} contacts per sync.", 400)

    seen = {row["phone"] for row in list_contacts(g.phone)}
    encrypt = encrypting()
    new_rows, skipped = [], 0
    for item in incoming:
        if not isinstance(item, dict):
            skipped += 1
            continue
        name = str(item.get("name") or "").strip()[:60]
        phone = normalize_contact_phone(str(item.get("phone") or ""))
        if not name or not phone or phone in seen:
            skipped += 1
            continue
        seen.add(phone)
        new_rows.append((g.phone, *stored_contact(name, phone, encrypt)))

    if new_rows:
        db = get_db()
        cur = db.cursor()
        cur.executemany(sql("INSERT INTO contacts (owner, name, phone, enc) VALUES (?, ?, ?, ?)"), new_rows)
        db.commit()

    return jsonify({"added": len(new_rows), "skipped": skipped, "total": len(seen)})
