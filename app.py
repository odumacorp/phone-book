"""
Phone Book USSD service for Africa's Talking (*384*88041#).

Africa's Talking POSTs to /ussd with form fields:
  sessionId, serviceCode, phoneNumber, networkCode, text
`text` is every input in the session joined by "*" (e.g. "1*John*0712345678").
We reply with plain text starting with "CON " (keep session open) or "END " (close it).
"""
import os
import re
import sqlite3
import threading
import logging

from dotenv import load_dotenv
from flask import Flask, request, Response, g

load_dotenv()

AT_USERNAME = os.getenv("AT_USERNAME", "sandbox")
AT_API_KEY = os.getenv("AT_API_KEY", "")
AT_SENDER_ID = os.getenv("AT_SENDER_ID") or None
SERVICE_CODE = os.getenv("USSD_SERVICE_CODE", "*384*88041#")
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "phonebook.db"))

USSD_MAX_LEN = 182  # max characters a USSD screen can show
PAGE_SIZE = 5

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ussd")

app = Flask(__name__)

sms = None
if AT_API_KEY:
    import africastalking

    africastalking.initialize(AT_USERNAME, AT_API_KEY)
    sms = africastalking.SMS
else:
    log.warning("AT_API_KEY not set - SMS features are disabled")


# ---------------------------------------------------------------- database

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS contacts (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   owner TEXT NOT NULL,
                   name TEXT NOT NULL,
                   phone TEXT NOT NULL,
                   created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_contacts_owner ON contacts(owner)")


def list_contacts(owner):
    return get_db().execute(
        "SELECT id, name, phone FROM contacts WHERE owner = ? ORDER BY name COLLATE NOCASE",
        (owner,),
    ).fetchall()


def search_contacts(owner, query):
    return get_db().execute(
        "SELECT id, name, phone FROM contacts WHERE owner = ? AND name LIKE ? "
        "ORDER BY name COLLATE NOCASE LIMIT ?",
        (owner, f"%{query}%", PAGE_SIZE),
    ).fetchall()


def add_contact(owner, name, phone):
    db = get_db()
    db.execute("INSERT INTO contacts (owner, name, phone) VALUES (?, ?, ?)", (owner, name, phone))
    db.commit()


def delete_contact(owner, contact_id):
    db = get_db()
    db.execute("DELETE FROM contacts WHERE id = ? AND owner = ?", (contact_id, owner))
    db.commit()


# ---------------------------------------------------------------- helpers

PHONE_RE = re.compile(r"^\+?\d{9,15}$")


def normalize_phone(raw):
    """Accept 0712345678 / 254712345678 / +254712345678 and return +254... form."""
    phone = raw.strip().replace(" ", "")
    if not PHONE_RE.match(phone):
        return None
    if phone.startswith("+"):
        return phone
    if phone.startswith("0"):
        return "+254" + phone[1:]
    return "+" + phone


def con(msg):
    return "CON " + msg[: USSD_MAX_LEN - 4]


def end(msg):
    return "END " + msg[: USSD_MAX_LEN - 4]


def format_contacts(rows, numbered=False):
    lines = []
    for i, row in enumerate(rows, 1):
        prefix = f"{i}. " if numbered else ""
        lines.append(f"{prefix}{row['name']} {row['phone']}")
    return "\n".join(lines)


def send_sms_async(to, message):
    def _send():
        try:
            resp = sms.send(message, [to], AT_SENDER_ID)
            log.info("SMS sent to %s: %s", to, resp)
        except Exception:
            log.exception("SMS to %s failed", to)

    threading.Thread(target=_send, daemon=True).start()


MAIN_MENU = (
    "Welcome to Phone Book\n"
    "1. Add contact\n"
    "2. Search contact\n"
    "3. My contacts\n"
    "4. Delete contact\n"
    "5. SMS me my contacts"
)


# ---------------------------------------------------------------- menu logic

def handle(phone_number, text):
    parts = text.split("*") if text else []
    if not parts:
        return con(MAIN_MENU)

    choice, rest = parts[0], parts[1:]

    # 1. Add contact: 1 -> name -> phone
    if choice == "1":
        if len(rest) == 0:
            return con("Enter contact name:")
        name = rest[0].strip()
        if not name:
            return end("Name cannot be empty.")
        if len(rest) == 1:
            return con(f"Enter phone number for {name}:")
        phone = normalize_phone(rest[1])
        if not phone:
            return end("Invalid phone number. Please try again.")
        add_contact(phone_number, name, phone)
        return end(f"Saved {name} - {phone}")

    # 2. Search: 2 -> query
    if choice == "2":
        if len(rest) == 0:
            return con("Enter name to search:")
        query = rest[0].strip()
        rows = search_contacts(phone_number, query)
        if not rows:
            return end(f"No contacts match '{query}'.")
        return end(format_contacts(rows))

    # 3. List, paginated: 3 -> 9 (more) -> 9 ...
    if choice == "3":
        rows = list_contacts(phone_number)
        if not rows:
            return end("You have no contacts yet.")
        if any(p != "9" for p in rest):
            return end("Invalid choice.")
        page = len(rest)
        chunk = rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        if not chunk:
            return end("No more contacts.")
        body = format_contacts(chunk)
        if (page + 1) * PAGE_SIZE < len(rows):
            return con(body + "\n9. More")
        return end(body)

    # 4. Delete: 4 -> pick number -> 1 confirm / 2 cancel
    if choice == "4":
        rows = list_contacts(phone_number)[:PAGE_SIZE * 2]
        if not rows:
            return end("You have no contacts to delete.")
        if len(rest) == 0:
            return con("Select contact to delete:\n" + format_contacts(rows, numbered=True))
        if not rest[0].isdigit() or not 1 <= int(rest[0]) <= len(rows):
            return end("Invalid selection.")
        contact = rows[int(rest[0]) - 1]
        if len(rest) == 1:
            return con(f"Delete {contact['name']} {contact['phone']}?\n1. Yes\n2. No")
        if rest[1] == "1":
            delete_contact(phone_number, contact["id"])
            return end(f"{contact['name']} deleted.")
        return end("Cancelled.")

    # 5. SMS full phone book to the caller
    if choice == "5":
        if sms is None:
            return end("SMS service is not available right now.")
        rows = list_contacts(phone_number)
        if not rows:
            return end("You have no contacts yet.")
        send_sms_async(phone_number, "Your contacts:\n" + format_contacts(rows))
        return end("You will receive your contacts by SMS shortly.")

    return end("Invalid choice. Please dial again.")


@app.route("/ussd", methods=["POST"])
def ussd():
    session_id = request.values.get("sessionId", "")
    service_code = request.values.get("serviceCode", "")
    phone_number = request.values.get("phoneNumber", "")
    text = request.values.get("text", "")
    log.info("session=%s code=%s phone=%s text=%r", session_id, service_code, phone_number, text)

    if SERVICE_CODE and service_code and service_code != SERVICE_CODE:
        body = end("Unknown service.")
    elif not phone_number:
        body = end("Could not identify your phone number.")
    else:
        try:
            body = handle(phone_number, text)
        except Exception:
            log.exception("Error handling USSD request")
            body = end("Something went wrong. Please try again.")

    return Response(body, mimetype="text/plain")


@app.route("/", methods=["GET"])
def health():
    return "Phone Book USSD service is running"


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)), debug=True)
