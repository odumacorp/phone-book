"""
Phone Book USSD service for Africa's Talking (*384*88041#).

Africa's Talking POSTs to /ussd with form fields:
  sessionId, serviceCode, phoneNumber, networkCode, text
`text` is every input in the session joined by "*" (e.g. "1*John*0712345678").
We reply with plain text starting with "CON " (keep session open) or "END " (close it).

The mobile app logs in with phone number + PIN (set via USSD option 6) and
uploads the device's contacts to /api/contacts/sync.
"""
import os
import re
import sqlite3
import threading
import time
import logging
from functools import wraps
from xml.sax.saxutils import quoteattr

from dotenv import load_dotenv
from flask import Flask, request, Response, g, jsonify
from itsdangerous import URLSafeTimedSerializer, BadSignature
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()

AT_USERNAME = os.getenv("AT_USERNAME", "sandbox")
AT_API_KEY = os.getenv("AT_API_KEY", "")
AT_SENDER_ID = os.getenv("AT_SENDER_ID") or None
AT_VOICE_NUMBER = os.getenv("AT_VOICE_NUMBER") or None  # AT virtual number used for "Call"
DATABASE_URL = os.getenv("DATABASE_URL")  # Postgres (Neon pooled); falls back to SQLite when unset
DATABASE_URL_UNPOOLED = os.getenv("DATABASE_URL_UNPOOLED") or DATABASE_URL  # direct, for schema setup
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "phonebook.db"))
SECRET_KEY = os.getenv("SECRET_KEY", "")

USSD_MAX_LEN = 182  # max characters a USSD screen can show
PAGE_SIZE = 4  # contacts per screen; leaves room for "9. More" and the pick hint
NAME_MAX = 15  # names are shortened on list screens so a full page fits
SMS_MAX = 140
CALL_TTL = 300  # seconds a requested call stays valid while we ring the caller

TOKEN_MAX_AGE = 30 * 24 * 3600  # app stays logged in for 30 days
MAX_PIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60
MAX_SYNC_CONTACTS = 5000

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ussd")

app = Flask(__name__)

sms = None
voice = None
if AT_API_KEY:
    import africastalking

    africastalking.initialize(AT_USERNAME, AT_API_KEY)
    sms = africastalking.SMS
    voice = africastalking.Voice
else:
    log.warning("AT_API_KEY not set - SMS features are disabled")

if DATABASE_URL:
    import psycopg
    from psycopg.rows import dict_row

if not SECRET_KEY:
    log.warning("SECRET_KEY not set - mobile app login is disabled")


# ---------------------------------------------------------------- database

def connect(url=None):
    if DATABASE_URL:
        # prepare_threshold=None: no server-side prepared statements through Neon's PgBouncer pooler
        return psycopg.connect(url or DATABASE_URL, row_factory=dict_row, prepare_threshold=None)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def sql(query):
    """Queries are written with SQLite's ? placeholders; Postgres wants %s."""
    return query.replace("?", "%s") if DATABASE_URL else query


def fetch_all(query, params=()):
    return get_db().execute(sql(query), params).fetchall()


def fetch_one(query, params=()):
    return get_db().execute(sql(query), params).fetchone()


def execute(query, params=()):
    db = get_db()
    db.execute(sql(query), params)
    db.commit()


def init_db():
    id_col = "SERIAL PRIMARY KEY" if DATABASE_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
    statements = [
        f"""CREATE TABLE IF NOT EXISTS contacts (
               id {id_col},
               owner TEXT NOT NULL,
               name TEXT NOT NULL,
               phone TEXT NOT NULL,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
           )""",
        "CREATE INDEX IF NOT EXISTS idx_contacts_owner ON contacts(owner)",
        """CREATE TABLE IF NOT EXISTS users (
               phone TEXT PRIMARY KEY,
               pin_hash TEXT NOT NULL,
               failed_attempts INTEGER NOT NULL DEFAULT 0,
               locked_until INTEGER NOT NULL DEFAULT 0
           )""",
        """CREATE TABLE IF NOT EXISTS pending_calls (
               user_phone TEXT PRIMARY KEY,
               contact_phone TEXT NOT NULL,
               created_at INTEGER NOT NULL
           )""",
    ]
    db = connect(DATABASE_URL_UNPOOLED)
    try:
        if DATABASE_URL:
            # gunicorn workers start together; concurrent CREATE TABLE IF NOT EXISTS can collide
            db.execute("SELECT pg_advisory_xact_lock(88041)")
        for statement in statements:
            db.execute(statement)
        db.commit()
    finally:
        db.close()


def list_contacts(owner):
    return fetch_all(
        "SELECT id, name, phone FROM contacts WHERE owner = ? ORDER BY LOWER(name), id",
        (owner,),
    )


def search_contacts(owner, query):
    return fetch_all(
        "SELECT id, name, phone FROM contacts WHERE owner = ? AND LOWER(name) LIKE LOWER(?) "
        "ORDER BY LOWER(name), id LIMIT ?",
        (owner, f"%{query}%", PAGE_SIZE),
    )


def add_contact(owner, name, phone):
    execute("INSERT INTO contacts (owner, name, phone) VALUES (?, ?, ?)", (owner, name, phone))


def delete_contact(owner, contact_id):
    execute("DELETE FROM contacts WHERE id = ? AND owner = ?", (contact_id, owner))


def find_contact_by_phone(owner, phone):
    return fetch_one(
        "SELECT id, name, phone FROM contacts WHERE owner = ? AND phone = ? ORDER BY id LIMIT 1",
        (owner, phone),
    )


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


def get_user(phone):
    return fetch_one(
        "SELECT phone, pin_hash, failed_attempts, locked_until FROM users WHERE phone = ?",
        (phone,),
    )


def set_pin(phone, pin):
    """A PIN can be set only once; returns False if this number already has one."""
    if get_user(phone):
        return False
    execute("INSERT INTO users (phone, pin_hash) VALUES (?, ?)", (phone, generate_password_hash(pin)))
    return True


# ---------------------------------------------------------------- helpers

PHONE_RE = re.compile(r"^\+?\d{9,15}$")
PHONE_JUNK_RE = re.compile(r"[\s\-().]")
PIN_RE = re.compile(r"^\d{4}$")


def normalize_phone(raw):
    """Accept 0712345678 / 254712345678 / +254 712-345-678 and return +254... form."""
    phone = PHONE_JUNK_RE.sub("", raw or "")
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


def format_contacts(rows, numbered=False, short=False):
    lines = []
    for i, row in enumerate(rows, 1):
        prefix = f"{i}. " if numbered else ""
        name = row["name"][:NAME_MAX].rstrip() if short else row["name"]
        lines.append(f"{prefix}{name} {row['phone']}")
    return "\n".join(lines)


def send_sms_async(to, message):
    def _send():
        try:
            resp = sms.send(message, [to], AT_SENDER_ID)
            log.info("SMS sent to %s: %s", to, resp)
        except Exception:
            log.exception("SMS to %s failed", to)

    threading.Thread(target=_send, daemon=True).start()


def start_call_async(to):
    def _call():
        try:
            resp = voice.call(callFrom=AT_VOICE_NUMBER, callTo=[to])
            log.info("Call to %s started: %s", to, resp)
        except Exception:
            log.exception("Call to %s failed", to)

    threading.Thread(target=_call, daemon=True).start()


MAIN_MENU = (
    "Welcome to Remote Phone-Book\n"
    "1. Add contact\n"
    "2. Search contact\n"
    "3. My contacts\n"
    "4. Delete contact\n"
    "5. SMS me my contacts"
)
SET_PIN_ITEM = "\n6. Set app PIN"  # only shown until the number has a PIN
PIN_ALREADY_SET = "Your app PIN is already set. Use it to log in to the app."


# ---------------------------------------------------------------- menu logic

def handle(phone_number, text):
    parts = text.split("*") if text else []
    if not parts:
        return con(MAIN_MENU + ("" if get_user(phone_number) else SET_PIN_ITEM))

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

    # 2. Search: 2 -> query -> pick contact -> action
    if choice == "2":
        if len(rest) == 0:
            return con("Enter name to search:")
        query = rest[0].strip()
        rows = search_contacts(phone_number, query)
        if not rows:
            return end(f"No contacts match '{query}'.")
        if len(rest) == 1:
            return con(format_contacts(rows, numbered=True, short=True) + "\n" + PICK_HINT)
        return pick_and_act(phone_number, rows, rest[1], rest[2:])

    # 3. List, paginated: 3 -> 9 (more) -> 9 ... -> pick contact -> action
    if choice == "3":
        rows = list_contacts(phone_number)
        if not rows:
            return end("You have no contacts yet.")
        page, i = 0, 0
        while i < len(rest) and rest[i] == "9" and (page + 1) * PAGE_SIZE < len(rows):
            page, i = page + 1, i + 1
        chunk = rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        if i == len(rest):
            body = format_contacts(chunk, numbered=True, short=True)
            if (page + 1) * PAGE_SIZE < len(rows):
                body += "\n9. More"
            return con(body + "\n" + PICK_HINT)
        return pick_and_act(phone_number, chunk, rest[i], rest[i + 1:])

    # 4. Delete: 4 -> pick number -> 1 confirm / 2 cancel
    if choice == "4":
        rows = list_contacts(phone_number)[:PAGE_SIZE]
        if not rows:
            return end("You have no contacts to delete.")
        if len(rest) == 0:
            return con("Select contact to delete:\n" + format_contacts(rows, numbered=True, short=True))
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

    # 6. Set the mobile app PIN (once only): 6 -> pin -> confirm
    if choice == "6":
        if get_user(phone_number):
            return end(PIN_ALREADY_SET)
        if len(rest) == 0:
            return con("Enter a 4-digit PIN for the Phone Book app:")
        if not PIN_RE.match(rest[0]):
            return end("PIN must be exactly 4 digits.")
        if len(rest) == 1:
            return con("Confirm your PIN:")
        if rest[1] != rest[0]:
            return end("PINs do not match. Please try again.")
        if not set_pin(phone_number, rest[0]):
            return end(PIN_ALREADY_SET)
        return end(f"App PIN set. Log in to the app with {phone_number} and this PIN.")

    return end("Invalid choice. Please dial again.")


PICK_HINT = "Reply no. or a phone no."


def pick_and_act(owner, rows, selection, rest):
    """Resolve a list position (1-4) or a typed phone number, then run the contact menu."""
    selection = selection.strip()
    if selection.isdigit() and 1 <= int(selection) <= len(rows):
        contact = rows[int(selection) - 1]
    else:
        phone = normalize_phone(selection)
        if not phone:
            return end("Invalid choice. Enter a number from the list or a phone number.")
        contact = find_contact_by_phone(owner, phone) or {"name": phone, "phone": phone}
    return contact_menu(owner, contact, rest)


def contact_menu(owner, contact, rest):
    name, phone = contact["name"], contact["phone"]
    if len(rest) == 0:
        title = name if name == phone else f"{name}\n{phone}"
        return con(f"{title}\n1. Call\n2. Send SMS\n3. Copy number")

    action = rest[0]

    # Call: Africa's Talking rings the caller, then /voice bridges them to the contact
    if action == "1":
        if voice is None or not AT_VOICE_NUMBER:
            return end("Calling is not available yet. Choose 3 to get the number by SMS.")
        save_pending_call(owner, phone)
        start_call_async(owner)
        return end(f"We are calling you now. Answer to be connected to {name}.")

    if action in ("2", "3") and sms is None:
        return end("SMS service is not available right now.")

    # Send SMS: user types the message; it is sent to the contact
    if action == "2":
        if len(rest) == 1:
            return con(f"Type your message to {name}:")
        message = "*".join(rest[1:]).strip()[:SMS_MAX]  # the message itself may contain "*"
        if not message:
            return end("Message cannot be empty.")
        send_sms_async(phone, f"{message}\n- from {owner}")
        return end(f"Message sent to {name}.")

    # Copy number: USSD can't reach the clipboard, so text it to the user to copy/call/save
    if action == "3":
        send_sms_async(owner, f"{name}: {phone}" if name != phone else phone)
        whose = f"{name}'s number" if name != phone else "The number"
        return end(f"{whose} is on its way by SMS. Open it to copy, call or save it.")

    return end("Invalid choice.")


@app.route("/voice", methods=["POST"])
def voice_callback():
    """Africa's Talking voice callback: when the caller answers, dial the contact they picked."""
    if request.values.get("isActive") != "1":
        return Response("", mimetype="text/plain")
    contact_phone = None
    for number in (request.values.get("destinationNumber"), request.values.get("callerNumber")):
        if number and not contact_phone:
            contact_phone = pop_pending_call(number)
    if contact_phone:
        body = f"<Dial phoneNumbers={quoteattr(contact_phone)} />"
    else:
        body = "<Say>Sorry, this call could not be connected.</Say>"
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>'
    return Response(xml, mimetype="application/xml")


@app.route("/ussd", methods=["POST"])
def ussd():
    session_id = request.values.get("sessionId", "")
    service_code = request.values.get("serviceCode", "")
    phone_number = request.values.get("phoneNumber", "")
    text = request.values.get("text", "")
    # Don't log PINs typed in the "Set app PIN" flow
    logged_text = "6*<pin>" if text.startswith("6*") else text
    log.info("session=%s code=%s phone=%s text=%r", session_id, service_code, phone_number, logged_text)

    if not phone_number:
        body = end("Could not identify your phone number.")
    else:
        try:
            body = handle(phone_number, text)
        except Exception:
            log.exception("Error handling USSD request")
            body = end("Something went wrong. Please try again.")

    return Response(body, mimetype="text/plain")


# ---------------------------------------------------------------- mobile app API

def serializer():
    return URLSafeTimedSerializer(SECRET_KEY, salt="phonebook-app")


def make_token(user):
    # Tied to the current PIN hash, so resetting the PIN logs out old sessions
    return serializer().dumps({"phone": user["phone"], "v": user["pin_hash"][-12:]})


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
        if not user or user["pin_hash"][-12:] != data.get("v"):
            return api_error("Please log in again.", 401)
        g.phone = user["phone"]
        return view(*args, **kwargs)

    return wrapper


@app.route("/api/login", methods=["POST"])
def api_login():
    if not SECRET_KEY:
        return api_error("App login is not configured on the server.", 503)
    body = request.get_json(silent=True) or {}
    phone = normalize_phone(str(body.get("phone", "")))
    pin = str(body.get("pin", ""))
    if not phone or not PIN_RE.match(pin):
        return api_error("Enter your phone number and 4-digit PIN.", 400)

    user = get_user(phone)
    if not user:
        return api_error("No PIN set for this number. Dial *384*88041# and choose 6 to set one.", 401)

    now = int(time.time())
    if user["locked_until"] > now:
        minutes = (user["locked_until"] - now) // 60 + 1
        return api_error(f"Too many wrong PINs. Try again in {minutes} min.", 429)

    if not check_password_hash(user["pin_hash"], pin):
        attempts = user["failed_attempts"] + 1
        locked_until = now + LOCKOUT_SECONDS if attempts >= MAX_PIN_ATTEMPTS else 0
        execute(
            "UPDATE users SET failed_attempts = ?, locked_until = ? WHERE phone = ?",
            (0 if locked_until else attempts, locked_until, phone),
        )
        if locked_until:
            return api_error("Too many wrong PINs. Try again in 15 min.", 429)
        return api_error("Wrong phone number or PIN.", 401)

    if user["failed_attempts"]:
        execute("UPDATE users SET failed_attempts = 0 WHERE phone = ?", (phone,))
    return jsonify({"token": make_token(user), "phone": phone})


@app.route("/api/me", methods=["GET"])
@require_auth
def api_me():
    row = fetch_one("SELECT COUNT(*) AS n FROM contacts WHERE owner = ?", (g.phone,))
    return jsonify({"phone": g.phone, "contacts": row["n"]})


@app.route("/api/contacts/sync", methods=["POST"])
@require_auth
def api_sync():
    body = request.get_json(silent=True) or {}
    incoming = body.get("contacts")
    if not isinstance(incoming, list):
        return api_error("Expected a list of contacts.", 400)
    if len(incoming) > MAX_SYNC_CONTACTS:
        return api_error(f"At most {MAX_SYNC_CONTACTS} contacts per sync.", 400)

    seen = {row["phone"] for row in fetch_all("SELECT phone FROM contacts WHERE owner = ?", (g.phone,))}
    new_rows, skipped = [], 0
    for item in incoming:
        if not isinstance(item, dict):
            skipped += 1
            continue
        name = str(item.get("name") or "").strip()[:60]
        phone = normalize_phone(str(item.get("phone") or ""))
        if not name or not phone or phone in seen:
            skipped += 1
            continue
        seen.add(phone)
        new_rows.append((g.phone, name, phone))

    if new_rows:
        db = get_db()
        cur = db.cursor()
        cur.executemany(sql("INSERT INTO contacts (owner, name, phone) VALUES (?, ?, ?)"), new_rows)
        db.commit()

    return jsonify({"added": len(new_rows), "skipped": skipped, "total": len(seen)})


@app.route("/", methods=["GET"])
def health():
    return "Phone Book USSD service is running"


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)), debug=True)
