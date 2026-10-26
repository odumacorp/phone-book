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
import secrets
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
NAME_MAX = 14  # names are shortened on list screens so a full page + Back/Home fits
SMS_MAX = 140
CALL_TTL = 300  # seconds a requested call stays valid while we ring the caller

TOKEN_MAX_AGE = 30 * 24 * 3600  # app stays logged in for 30 days
MAX_PIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60
MAX_SYNC_CONTACTS = 5000
RESET_CODE_TTL = 10 * 60  # forgot-PIN SMS code lifetime
RESET_RESEND_SECONDS = 60
MAX_RESET_ATTEMPTS = 5

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
        """CREATE TABLE IF NOT EXISTS pin_resets (
               phone TEXT PRIMARY KEY,
               code_hash TEXT NOT NULL,
               expires_at INTEGER NOT NULL,
               sent_at INTEGER NOT NULL,
               attempts INTEGER NOT NULL DEFAULT 0
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


def update_pin(phone, pin):
    """Change or reset an existing PIN (app only). Clears any lockout; old app sessions stop working."""
    execute(
        "UPDATE users SET pin_hash = ?, failed_attempts = 0, locked_until = 0 WHERE phone = ?",
        (generate_password_hash(pin), phone),
    )
    return get_user(phone)


# ---------------------------------------------------------------- helpers

PHONE_RE = re.compile(r"^\+?\d{9,15}$")
PHONE_JUNK_RE = re.compile(r"[\s\-().]")
PIN_RE = re.compile(r"^\d{4}$")
LOGIN_PHONE_RE = re.compile(r"^(?:\+254\d{9}|07\d{8})$")
LOGIN_PHONE_FORMAT = "+254XXXXXXXXX or 07XXXXXXXX"


def login_phone(raw):
    """The number someone logs in with must be +254XXXXXXXXX or 07XXXXXXXX; returns +254... or None."""
    phone = PHONE_JUNK_RE.sub("", raw or "")
    if not LOGIN_PHONE_RE.match(phone):
        return None
    return "+254" + phone[1:] if phone.startswith("0") else phone


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
    "Remote Phone-Book\n"
    "1. Add contact\n"
    "2. Search contact\n"
    "3. My contacts\n"
    "4. Delete contact\n"
    "5. SMS me my contacts\n"
    "6. Change PIN"
)
BACK, HOME = "0", "00"
NAV = "0. Back  00. Home"
FORGOT_PIN = "99"


def screen(msg):
    """A screen inside the menu: always offers Back/Home, so a result or mistake doesn't end the session."""
    room = USSD_MAX_LEN - 4 - len(NAV) - 1
    return con(msg[:room] + "\n" + NAV)


def resolve_nav(inputs):
    """Apply 0 (back one step) and 00 (home) to the inputs typed after logging in."""
    path = []
    for value in inputs:
        if value == HOME:
            path = []
        elif value == BACK:
            path = path[:-1]
        else:
            path.append(value)
    return path


INVALID = "Invalid choice."


# ---------------------------------------------------------------- login

def check_pin(phone, pin):
    """Verify a PIN with lockout. Returns (user, None) on success or (None, error message)."""
    user = get_user(phone)
    if not user:
        return None, "Wrong phone number or PIN."
    now = int(time.time())
    if user["locked_until"] > now:
        minutes = (user["locked_until"] - now + 59) // 60  # round up
        return None, f"Too many wrong PINs. Try again in {minutes} min."
    if not check_password_hash(user["pin_hash"], pin):
        attempts = user["failed_attempts"] + 1
        locked_until = now + LOCKOUT_SECONDS if attempts >= MAX_PIN_ATTEMPTS else 0
        execute(
            "UPDATE users SET failed_attempts = ?, locked_until = ? WHERE phone = ?",
            (0 if locked_until else attempts, locked_until, phone),
        )
        if locked_until:
            return None, "Too many wrong PINs. Try again in 15 min."
        return None, "Wrong phone number or PIN."
    if user["failed_attempts"]:
        execute("UPDATE users SET failed_attempts = 0 WHERE phone = ?", (phone,))
    return user, None


def new_pin_flow(inputs, prompt, done):
    """Shared by first-time setup and forgot-PIN: new PIN -> confirm. Calls done(pin) at the end."""
    if len(inputs) == 0:
        return con(prompt)
    if not PIN_RE.match(inputs[0]):
        return end("PIN must be exactly 4 digits.")
    if len(inputs) == 1:
        return con("Confirm your PIN:")
    if inputs[1] != inputs[0]:
        return end("PINs do not match. Please dial again.")
    return done(inputs[0])


def handle(caller, text):
    """Every session starts with: phone number -> PIN (or first-time PIN setup) -> main menu.

    The number entered decides whose phone book opens, so users can reach their contacts
    from any phone. SMS replies and calls go to `caller`, the phone actually in use.
    """
    parts = text.split("*") if text else []
    if not parts:
        return con("Welcome to Remote Phone-Book\nEnter your phone number:")

    owner = login_phone(parts[0])
    if not owner:
        return end(f"Invalid phone number. Use {LOGIN_PHONE_FORMAT}.")

    if not get_user(owner):
        # First time: a PIN can only be created from the phone that owns the number, once
        if owner != caller:
            return end(f"{owner} has no PIN yet. Dial from that phone to set one.")

        def create(pin):
            if not set_pin(owner, pin):
                return end("A PIN was already set for this number. Please dial again.")
            return end("PIN set. Dial again and log in with your phone number and PIN.")

        return new_pin_flow(parts[1:], "Create a 4-digit PIN for your phone book:", create)

    if len(parts) == 1:
        return con(f"Enter your PIN:\n{FORGOT_PIN}. Forgot PIN")

    # Forgot PIN: dialling from the number itself proves ownership (no old PIN needed)
    if parts[1] == FORGOT_PIN:
        if owner != caller:
            return end(f"To reset the PIN for {owner}, dial from that phone or use the Phone Book app.")

        def reset(pin):
            update_pin(owner, pin)
            return end("PIN reset. Dial again and log in with your new PIN.")

        return new_pin_flow(parts[2:], "Enter a new 4-digit PIN:", reset)

    _user, error = check_pin(owner, parts[1])
    if error:
        return end(f"{error}\nForgot it? Dial again and choose {FORGOT_PIN}, or use the app.")
    return menu(owner, caller, resolve_nav(parts[2:]))


# ---------------------------------------------------------------- menu logic
# Every request replays the whole path, so actions (save, delete, send, call) only run when
# the path ends exactly at them; anything typed after a result is rejected, not re-run.

def menu(owner, caller, parts):
    if not parts:
        return con(MAIN_MENU)

    choice, rest = parts[0], parts[1:]

    # 1. Add contact: 1 -> name -> phone
    if choice == "1":
        if len(rest) == 0:
            return screen("Enter contact name:")
        name = rest[0].strip()
        if not name:
            return screen("Name cannot be empty.")
        if len(rest) == 1:
            return screen(f"Enter phone number for {name}:")
        if len(rest) > 2:
            return screen(INVALID)
        phone = normalize_phone(rest[1])
        if not phone:
            return screen("Invalid phone number.")
        add_contact(owner, name, phone)
        return screen(f"Saved {name} - {phone}")

    # 2. Search: 2 -> query -> pick contact -> action
    if choice == "2":
        if len(rest) == 0:
            return screen("Enter name to search:")
        query = rest[0].strip()
        rows = search_contacts(owner, query)
        if not rows:
            return screen(f"No contacts match '{query}'.")
        if len(rest) == 1:
            return screen(format_contacts(rows, numbered=True, short=True) + "\n" + PICK_HINT)
        return pick_and_act(owner, caller, rows, rest[1], rest[2:])

    # 3. List, paginated: 3 -> 9 (more) -> 9 ... -> pick contact -> action
    if choice == "3":
        rows = list_contacts(owner)
        if not rows:
            return screen("You have no contacts yet.")
        page, i = 0, 0
        while i < len(rest) and rest[i] == "9" and (page + 1) * PAGE_SIZE < len(rows):
            page, i = page + 1, i + 1
        chunk = rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        if i == len(rest):
            body = format_contacts(chunk, numbered=True, short=True)
            if (page + 1) * PAGE_SIZE < len(rows):
                body += "\n9. More"
            return screen(body + "\n" + PICK_HINT)
        return pick_and_act(owner, caller, chunk, rest[i], rest[i + 1:])

    # 4. Delete: 4 -> pick number -> 1 confirm / 2 cancel
    if choice == "4":
        rows = list_contacts(owner)[:PAGE_SIZE]
        if not rows:
            return screen("You have no contacts to delete.")
        if len(rest) == 0:
            return screen("Select contact to delete:\n" + format_contacts(rows, numbered=True, short=True))
        if not rest[0].isdigit() or not 1 <= int(rest[0]) <= len(rows):
            return screen("Invalid selection.")
        contact = rows[int(rest[0]) - 1]
        if len(rest) == 1:
            return screen(f"Delete {contact['name']} {contact['phone']}?\n1. Yes\n2. No")
        if len(rest) > 2:
            return screen(INVALID)
        if rest[1] == "1":
            delete_contact(owner, contact["id"])
            return screen(f"{contact['name']} deleted.")
        return screen("Cancelled.")

    # 5. SMS full phone book to the phone in use
    if choice == "5":
        if rest:
            return screen(INVALID)
        if sms is None:
            return screen("SMS service is not available right now.")
        rows = list_contacts(owner)
        if not rows:
            return screen("You have no contacts yet.")
        send_sms_async(caller, "Your contacts:\n" + format_contacts(rows))
        return screen("You will receive your contacts by SMS shortly.")

    # 6. Change PIN: 6 -> new PIN -> confirm. Ends the session (this session logged in with the old PIN)
    if choice == "6":
        if len(rest) == 0:
            return screen("Enter a new 4-digit PIN:")
        if not PIN_RE.match(rest[0]):
            return screen("PIN must be exactly 4 digits.")
        if check_password_hash(get_user(owner)["pin_hash"], rest[0]):
            return screen("New PIN must be different from your current PIN.")
        if len(rest) == 1:
            return screen("Confirm new PIN:")
        if len(rest) > 2:
            return screen(INVALID)
        if rest[1] != rest[0]:
            return screen("PINs do not match.")
        update_pin(owner, rest[0])
        return end("PIN changed. Use your new PIN next time you dial or open the app.")

    return screen(INVALID)


PICK_HINT = "Reply no. or phone no."


def pick_and_act(owner, caller, rows, selection, rest):
    """Resolve a list position (1-4) or a typed phone number, then run the contact menu."""
    selection = selection.strip()
    if selection.isdigit() and 1 <= int(selection) <= len(rows):
        contact = rows[int(selection) - 1]
    else:
        phone = normalize_phone(selection)
        if not phone:
            return screen("Enter a number from the list or a phone number.")
        contact = find_contact_by_phone(owner, phone) or {"name": phone, "phone": phone}
    return contact_menu(owner, caller, contact, rest)


def contact_menu(owner, caller, contact, rest):
    name, phone = contact["name"], contact["phone"]
    if len(rest) == 0:
        title = name if name == phone else f"{name}\n{phone}"
        return screen(f"{title}\n1. Call\n2. Send SMS\n3. Copy number")

    action = rest[0]
    if len(rest) > (2 if action == "2" else 1):
        return screen(INVALID)

    # Call: Africa's Talking rings the caller, then /voice bridges them to the contact
    if action == "1":
        if voice is None or not AT_VOICE_NUMBER:
            return screen("Calling is not available yet. Choose 3 to get the number by SMS.")
        save_pending_call(caller, phone)
        start_call_async(caller)
        return end(f"We are calling you now. Answer to be connected to {name}.")

    if action in ("2", "3") and sms is None:
        return screen("SMS service is not available right now.")

    # Send SMS: user types the message; it is sent to the contact
    if action == "2":
        if len(rest) == 1:
            return screen(f"Type your message to {name}:")
        message = rest[1].strip()[:SMS_MAX]
        if not message:
            return screen("Message cannot be empty.")
        send_sms_async(phone, f"{message}\n- from {owner}")
        return screen(f"Message sent to {name}.")

    # Copy number: USSD can't reach the clipboard, so text it to the user to copy/call/save
    if action == "3":
        send_sms_async(caller, f"{name}: {phone}" if name != phone else phone)
        whose = f"{name}'s number" if name != phone else "The number"
        return screen(f"{whose} is on its way by SMS. Open it to copy, call or save it.")

    return screen(INVALID)


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
    # PINs (login, setup, reset, change) are 4-digit inputs - never log them
    logged = ["<pin>" if PIN_RE.match(value) else value for value in text.split("*")]
    log.info("session=%s code=%s phone=%s text=%r", session_id, service_code, phone_number, "*".join(logged))

    caller = normalize_phone(phone_number)
    if not caller:
        body = end("Could not identify your phone number.")
    else:
        try:
            body = handle(caller, text)
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
    phone = login_phone(str(body.get("phone", "")))
    pin = str(body.get("pin", ""))
    if not phone:
        return api_error(f"Enter your phone number as {LOGIN_PHONE_FORMAT}.", 400)
    if not PIN_RE.match(pin):
        return api_error("Enter your 4-digit PIN.", 400)

    if not get_user(phone):
        return api_error(NO_PIN_YET, 401)

    user, error = check_pin(phone, pin)
    if error:
        return api_error(error, 429 if error.startswith("Too many") else 401)
    return jsonify({"token": make_token(user), "phone": phone})


NO_PIN_YET = "No PIN set for this number. Dial *384*88041# from this phone to create one."


def pin_error(new_pin, confirm):
    if not PIN_RE.match(new_pin):
        return "New PIN must be exactly 4 digits."
    if confirm is not None and confirm != new_pin:
        return "New PINs do not match."
    return None


@app.route("/api/pin/change", methods=["POST"])
@require_auth
def api_change_pin():
    body = request.get_json(silent=True) or {}
    current, new_pin = str(body.get("current_pin", "")), str(body.get("new_pin", ""))
    error = pin_error(new_pin, str(body.get("confirm_pin", new_pin)))
    if error:
        return api_error(error, 400)
    if new_pin == current:
        return api_error("New PIN must be different from your current PIN.", 400)
    _user, error = check_pin(g.phone, current)  # wrong current PINs count toward the lockout
    if error:
        if error.startswith("Wrong"):
            error = "Current PIN is wrong."
        return api_error(error, 429 if error.startswith("Too many") else 401)
    user = update_pin(g.phone, new_pin)
    return jsonify({"token": make_token(user), "phone": g.phone})


@app.route("/api/pin/reset/request", methods=["POST"])
def api_reset_request():
    """Forgot PIN, step 1: text a 6-digit code to the number (proves the user has that SIM)."""
    body = request.get_json(silent=True) or {}
    phone = login_phone(str(body.get("phone", "")))
    if not phone:
        return api_error(f"Enter your phone number as {LOGIN_PHONE_FORMAT}.", 400)
    if sms is None:
        return api_error("SMS service is not available right now.", 503)
    if not get_user(phone):
        return api_error(NO_PIN_YET, 404)

    now = int(time.time())
    row = fetch_one("SELECT sent_at FROM pin_resets WHERE phone = ?", (phone,))
    if row and now - row["sent_at"] < RESET_RESEND_SECONDS:
        return api_error("A code was just sent. Wait a minute before asking for another.", 429)

    code = f"{secrets.randbelow(10 ** 6):06d}"
    db = get_db()
    db.execute(sql("DELETE FROM pin_resets WHERE phone = ?"), (phone,))
    db.execute(
        sql("INSERT INTO pin_resets (phone, code_hash, expires_at, sent_at) VALUES (?, ?, ?, ?)"),
        (phone, generate_password_hash(code), now + RESET_CODE_TTL, now),
    )
    db.commit()
    send_sms_async(phone, f"Your Remote Phone-Book reset code is {code}. It expires in 10 minutes.")
    return jsonify({"sent": True, "phone": phone})


@app.route("/api/pin/reset/confirm", methods=["POST"])
def api_reset_confirm():
    """Forgot PIN, step 2: code from the SMS + new PIN. Logs the user in."""
    if not SECRET_KEY:
        return api_error("App login is not configured on the server.", 503)
    body = request.get_json(silent=True) or {}
    phone = login_phone(str(body.get("phone", "")))
    code, new_pin = str(body.get("code", "")).strip(), str(body.get("new_pin", ""))
    if not phone:
        return api_error(f"Enter your phone number as {LOGIN_PHONE_FORMAT}.", 400)
    error = pin_error(new_pin, str(body.get("confirm_pin", new_pin)))
    if error:
        return api_error(error, 400)

    row = fetch_one("SELECT code_hash, expires_at, attempts FROM pin_resets WHERE phone = ?", (phone,))
    if not row or row["expires_at"] < time.time():
        return api_error("This code has expired. Ask for a new one.", 400)
    if not check_password_hash(row["code_hash"], code):
        attempts = row["attempts"] + 1
        if attempts >= MAX_RESET_ATTEMPTS:
            execute("DELETE FROM pin_resets WHERE phone = ?", (phone,))
            return api_error("Too many wrong codes. Ask for a new one.", 429)
        execute("UPDATE pin_resets SET attempts = ? WHERE phone = ?", (attempts, phone))
        return api_error("Wrong code.", 401)

    execute("DELETE FROM pin_resets WHERE phone = ?", (phone,))
    user = update_pin(phone, new_pin)
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
