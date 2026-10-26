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
import atexit
import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
import json
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
PAGE_SIZE = 4  # contacts per screen; leaves room for "98. More" and the pick hint
NAME_MAX = 13  # names are shortened on list screens so a full page + Back/Home fits
MORE = "98"  # next page on the contact list (contact numbers skip 98)
SMS_MAX = 160  # one SMS
CALL_TTL = 300  # seconds a requested call stays valid while we ring the caller
RESUME_SECONDS = 2 * 60  # a USSD login stays active this long after the last screen (inactivity timeout)

TOKEN_MAX_AGE = 30 * 24 * 3600  # app stays logged in for 30 days
MAX_PIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60
MAX_SYNC_CONTACTS = 5000
MAX_PRIORITY = 5  # top contacts the user pins in the app
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
    from psycopg_pool import ConnectionPool

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


_pool = None
_pool_lock = threading.Lock()


def pool():
    """Reuse Postgres connections across requests: opening one to Neon costs several round trips.

    Created on first use so each gunicorn worker gets its own (pools don't survive fork).
    """
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ConnectionPool(
                DATABASE_URL,
                min_size=1,
                max_size=4,
                max_idle=240,  # Neon may close idle connections; drop ours first
                check=ConnectionPool.check_connection,  # replace connections Neon closed while idle
                kwargs={"row_factory": dict_row, "prepare_threshold": None},
                open=True,
            )
            atexit.register(_pool.close)
    return _pool


def get_db():
    if "db" not in g:
        g.db = pool().getconn() if DATABASE_URL else connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is None:
        return
    if DATABASE_URL:
        pool().putconn(db)  # rolls back anything uncommitted
    else:
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
        """CREATE TABLE IF NOT EXISTS ussd_resume (
               caller TEXT PRIMARY KEY,
               owner TEXT NOT NULL,
               path TEXT NOT NULL,
               saved_at INTEGER NOT NULL,
               session_id TEXT NOT NULL,
               offered_in TEXT,
               resume_owner TEXT,
               resume_from TEXT
           )""",
        f"""CREATE TABLE IF NOT EXISTS login_logs (
               id {id_col},
               owner TEXT NOT NULL,
               caller TEXT NOT NULL,
               network TEXT,
               session_id TEXT,
               success INTEGER NOT NULL,
               reason TEXT,
               created_at INTEGER NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_login_logs_owner ON login_logs(owner, created_at)",
        """CREATE TABLE IF NOT EXISTS revoked_tokens (
               jti TEXT PRIMARY KEY,
               expires_at INTEGER NOT NULL
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
        # Columns added after launch; existing databases get them on the next start
        add_column(db, "contacts", "priority", "INTEGER NOT NULL DEFAULT 0")
        add_column(db, "users", "show_others", "INTEGER NOT NULL DEFAULT 1")
        add_column(db, "users", "name", "TEXT")
        # App accounts: email (optional) or phone + a password of the user's choosing
        add_column(db, "users", "email", "TEXT")
        add_column(db, "users", "password_hash", "TEXT")
        add_column(db, "users", "pw_failed_attempts", "INTEGER NOT NULL DEFAULT 0")
        add_column(db, "users", "pw_locked_until", "INTEGER NOT NULL DEFAULT 0")
        add_column(db, "users", "token_epoch", "INTEGER NOT NULL DEFAULT 0")  # bumped by "log out everywhere"
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        add_column(db, "ussd_resume", "pin_mac", "TEXT")
        add_column(db, "ussd_resume", "logged_out", "INTEGER NOT NULL DEFAULT 0")  # ended from the app
        db.commit()
    finally:
        db.close()


def add_column(db, table, column, definition):
    if DATABASE_URL:
        db.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")
    elif column not in [row[1] for row in db.execute(f"PRAGMA table_info({table})")]:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


CONTACT_ORDER = "ORDER BY priority DESC, LOWER(name), id"  # priority contacts first


def list_contacts(owner, priority_only=False):
    only = " AND priority = 1" if priority_only else ""
    return fetch_all(
        f"SELECT id, name, phone, priority FROM contacts WHERE owner = ?{only} {CONTACT_ORDER}",
        (owner,),
    )


def search_contacts(owner, query, priority_only=False):
    only = " AND priority = 1" if priority_only else ""
    return fetch_all(
        f"SELECT id, name, phone, priority FROM contacts WHERE owner = ? AND LOWER(name) LIKE LOWER(?){only} "
        f"{CONTACT_ORDER} LIMIT ?",
        (owner, f"%{query}%", PAGE_SIZE),
    )


def priority_only(owner):
    """USSD shows only priority contacts when the user turned off "show other contacts" in the app."""
    return not get_user(owner)["show_others"]


def add_contact(owner, name, phone):
    g.action_done = True
    execute("INSERT INTO contacts (owner, name, phone) VALUES (?, ?, ?)", (owner, name, phone))


def delete_contact(owner, contact_id):
    g.action_done = True
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


USER_COLUMNS = (
    "phone, pin_hash, failed_attempts, locked_until, show_others, name, "
    "email, password_hash, pw_failed_attempts, pw_locked_until, token_epoch"
)


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
    return get_user(phone, fresh=True)


# ---------------------------------------------------------------- helpers

PHONE_RE = re.compile(r"^\+?\d{9,15}$")
PHONE_JUNK_RE = re.compile(r"[\s\-().]")
PIN_RE = re.compile(r"^\d{4}$")
LOGIN_PHONE_RE = re.compile(r"^(?:\+254\d{9}|0\d{9})$")
LOGIN_PHONE_FORMAT = "+254XXXXXXXXX or 0XXXXXXXXX"


def login_phone(raw):
    """The number someone logs in with must be +254XXXXXXXXX or 0XXXXXXXXX (07.., 01..); returns +254... or None."""
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


def label(position):
    """Number shown for the Nth contact in a list. Numbering continues across pages and skips 98 (More)."""
    return position if position < int(MORE) else position + 1


def position_of(choice, first, count):
    """Index into a page whose first contact is number `first`, or None if `choice` isn't on it."""
    if not choice.isdigit() or choice == MORE:
        return None
    n = int(choice)
    position = n if n < int(MORE) else n - 1
    index = position - first
    return index if 0 <= index < count else None


def format_contacts(rows, numbered=False, short=False, first=1):
    lines = []
    for i, row in enumerate(rows, first):
        prefix = f"{label(i)}. " if numbered else ""
        name = row["name"][:NAME_MAX].rstrip() if short else row["name"]
        lines.append(f"{prefix}{name} {row['phone']}")
    return "\n".join(lines)


def send_sms_async(to, message):
    g.action_done = True  # a USSD path that sent an SMS must not be resumed (it would send again)

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
    "Or type a name to search"
)
BACK, HOME = "0", "00"
NAV = "0. Back  00. Home"


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
NO_PRIORITY = "No priority contacts yet. Choose up to 5 in the Remote Phonebook app."


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


# ---------------------------------------------------------------- resume after a timeout
# Networks end USSD sessions after a few minutes (and sooner when a screen sits idle); we can't
# extend that. Instead each phone's last menu screen is saved, and redialling within
# RESUME_SECONDS asks only for the PIN and returns there. The session that was offered the
# resume is remembered (offered_in) so its later inputs are read the same way.

def get_resume(caller):
    return fetch_one(
        "SELECT owner, path, saved_at, session_id, offered_in, resume_owner, resume_from, pin_mac, logged_out "
        "FROM ussd_resume WHERE caller = ?",
        (caller,),
    )


def save_resume(caller, owner, path, session_id, state, mac):
    keep = state is not None and state["offered_in"] == session_id
    execute(
        "INSERT INTO ussd_resume (caller, owner, path, saved_at, session_id, offered_in, resume_owner, resume_from, pin_mac, logged_out) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0) "
        "ON CONFLICT (caller) DO UPDATE SET owner = excluded.owner, path = excluded.path, saved_at = excluded.saved_at, "
        "session_id = excluded.session_id, offered_in = excluded.offered_in, resume_owner = excluded.resume_owner, "
        "resume_from = excluded.resume_from, pin_mac = excluded.pin_mac, logged_out = 0",
        (
            caller,
            owner,
            json.dumps(path),
            int(time.time()),
            session_id,
            session_id if keep else None,
            state["resume_owner"] if keep else None,
            state["resume_from"] if keep else None,
            mac,
        ),
    )


def session_mac(session_id, owner, pin):
    """Proof that this PIN already passed the (deliberately slow) check earlier in this USSD session."""
    if not SECRET_KEY or not session_id:
        return None
    msg = f"{session_id}|{owner}|{pin}".encode()
    return hmac.new(SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


def login(owner, pin, session_id, state, caller, network):
    """check_pin, but only the first time in a session: every screen resends the PIN."""
    mac = session_mac(session_id, owner, pin)
    if mac and state and state["session_id"] == session_id and state["owner"] == owner and state["pin_mac"]:
        user = get_user(owner)
        unlocked = user and user["locked_until"] <= time.time()  # a lockout still applies mid-session
        if unlocked and hmac.compare_digest(state["pin_mac"], mac):
            return mac, None
    _user, error = check_pin(owner, pin)
    log_login(owner, caller, network, session_id, error)
    return mac, error


# ---------------------------------------------------------------- USSD login history
# One row per login attempt (not per screen), shown in the app. USSD only tells us the dialling
# number and its network, so that's what identifies the device.

LOGIN_LOG_DAYS = 90
NETWORKS = {"63902": "Safaricom", "63903": "Airtel", "63907": "Telkom", "99999": "Sandbox"}


def log_login(owner, caller, network, session_id, error, resumed=False):
    success = error is None
    if success and session_id and fetch_one(
        "SELECT 1 AS x FROM login_logs WHERE owner = ? AND session_id = ? AND success = 1", (owner, session_id)
    ):
        return  # already logged this session (only happens without SECRET_KEY, when every screen re-checks)
    if resumed:
        reason = "resumed"  # own phone, within the inactivity window: no PIN asked
    elif success:
        reason = "ok"
    elif error.startswith("Too many"):
        reason = "locked"
    else:
        reason = "wrong_pin"
    now = int(time.time())
    db = get_db()
    db.execute(
        sql(
            "INSERT INTO login_logs (owner, caller, network, session_id, success, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)"
        ),
        (owner, caller, network or None, session_id or None, int(success), reason, now),
    )
    db.execute(
        sql("DELETE FROM login_logs WHERE owner = ? AND created_at < ?"), (owner, now - LOGIN_LOG_DAYS * 86400)
    )
    db.commit()


def offer_resume(caller, session_id):
    execute(
        "UPDATE ussd_resume SET offered_in = ?, resume_owner = owner, resume_from = path WHERE caller = ?",
        (session_id, caller),
    )


def trusted_resume(state, caller, offered=False):
    """Continue without the PIN only on the owner's own phone, and only if the account isn't locked.

    A borrowed phone always asks for the PIN again, so the next person to use it can't get in.
    """
    owner = state["resume_owner"] if offered else state["owner"]
    if owner != caller:
        return False
    user = get_user(owner)
    return bool(user) and user["locked_until"] <= time.time()


def masked(phone):
    return "0" + phone[4:7] + "***" + phone[-3:] if phone.startswith("+254") else phone


def run_menu(owner, caller, path, session_id, state, mac):
    """Show a menu screen and remember it for resuming (never a path that just ran an action)."""
    g.action_done = False
    body = menu(owner, caller, path)
    if body.startswith("CON "):
        save_resume(caller, owner, [] if g.action_done else path, session_id, state, mac)
    else:
        execute("DELETE FROM ussd_resume WHERE caller = ?", (caller,))
    return body


def handle(caller, text, session_id="", network=""):
    """Every session starts with: phone number -> PIN -> main menu (numbers register in the app).

    The number entered decides whose phone book opens, so users can reach their contacts
    from any phone. SMS replies and calls go to `caller`, the phone actually in use.
    """
    parts = text.split("*") if text else []
    state = get_resume(caller)

    if state and state["logged_out"]:
        # Logged out from the app. Every screen resends the phone number and PIN, so an open
        # session would otherwise carry on: end it, and don't offer to continue on the next dial.
        if session_id and session_id in (state["session_id"], state["offered_in"]):
            return end("You were logged out from the Remote Phonebook app. Dial again to log in.")
        execute("DELETE FROM ussd_resume WHERE caller = ?", (caller,))
        state = None

    if not parts:
        fresh = state and time.time() - state["saved_at"] <= RESUME_SECONDS
        if fresh and state["session_id"] != session_id:
            offer_resume(caller, session_id)
            if trusted_resume(state, caller):
                return con(f"Welcome back {masked(state['owner'])}\n1. Continue where you left off\n2. Main menu\n0. Log out")
            return con(f"Welcome back. Enter PIN for {masked(state['owner'])} to continue where you left off:\n0. Start over")
        # Logged-in state that went RESUME_SECONDS without activity is a logout: delete it (and any
        # other expired ones) so nothing about the old login stays usable
        expired = state and state["saved_at"] and not fresh
        execute("DELETE FROM ussd_resume WHERE saved_at < ?", (int(time.time()) - RESUME_SECONDS,))
        if expired:
            minutes = RESUME_SECONDS // 60
            return con(
                f"Your session expired after {minutes} minute{'s' if minutes != 1 else ''} of inactivity. "
                "You were logged out for security.\nEnter your phone number:"
            )
        return con("Welcome to Remote Phone-Book\nEnter your phone number:")

    if state and state["offered_in"] == session_id and trusted_resume(state, caller, offered=True):
        # Own phone, still within the inactivity window: no PIN, just continue / main menu / log out
        if parts[0] in ("1", "2"):
            owner = state["resume_owner"]
            if len(parts) == 1:
                log_login(owner, caller, network, session_id, None, resumed=True)
            start = json.loads(state["resume_from"]) if parts[0] == "1" else []
            return run_menu(owner, caller, resolve_nav(start + parts[1:]), session_id, state, None)
        if parts[0] != BACK:
            return end("Invalid choice. Please dial again.")
        # Log out: end the active login but keep offered_in, so this session's later inputs
        # ("0*<phone>*<PIN>...") are still read as "start over" below
        execute("UPDATE ussd_resume SET resume_owner = NULL, resume_from = NULL, saved_at = 0 WHERE caller = ?", (caller,))
        state = get_resume(caller)
        parts = parts[1:]
        if not parts:
            return con("Logged out.\nEnter your phone number:")

    if state and state["offered_in"] == session_id:
        # This session started with the resume offer: first input is the PIN, or 0 to start over
        if parts[0] != BACK:
            owner = state["resume_owner"]
            if not owner:  # logged out earlier in this session; only "0 ..." (start over) is valid
                return end("Invalid choice. Please dial again.")
            mac, error = login(owner, parts[0], session_id, state, caller, network)
            if error:
                return end(f"{error}\nForgot your PIN? Set a new one in the Remote Phonebook app.")
            path = resolve_nav(json.loads(state["resume_from"]) + parts[1:])
            return run_menu(owner, caller, path, session_id, state, mac)
        parts = parts[1:]
        if not parts:
            return con("Enter your phone number:")

    owner = login_phone(parts[0])
    if not owner:
        return end(f"Invalid phone number. Use {LOGIN_PHONE_FORMAT}.")

    if not get_user(owner):
        return end(f"{owner} is not registered. Register in the Remote Phonebook app, then dial again.")

    if len(parts) == 1:
        return con("Enter your PIN:")
    mac, error = login(owner, parts[1], session_id, state, caller, network)
    if error:
        # PINs are changed or reset only in the mobile app
        return end(f"{error}\nForgot your PIN? Set a new one in the Remote Phonebook app.")
    return run_menu(owner, caller, resolve_nav(parts[2:]), session_id, state, mac)


# ---------------------------------------------------------------- menu logic
# Every request replays the whole path, so actions (save, delete, send, call) only run when
# the path ends exactly at them; anything typed after a result is rejected, not re-run.

def menu(owner, caller, parts):
    if not parts:
        return con(MAIN_MENU)

    choice, rest = parts[0], parts[1:]
    if choice.strip() and not choice.strip().isdigit():
        return menu(owner, caller, ["2"] + parts)  # typed a name on the main menu: search straight away
    only = priority_only(owner)
    none_yet = NO_PRIORITY if only else "You have no contacts yet."

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
        rows = search_contacts(owner, query, only)
        if not rows:
            return screen(f"No contacts match '{query}'.")
        if len(rest) == 1:
            return screen(format_contacts(rows, numbered=True, short=True) + "\n" + PICK_HINT)
        return pick_and_act(owner, caller, rows, rest[1], rest[2:])

    # 3. List, paginated: 3 -> 9 (more) -> 9 ... -> pick contact -> action
    if choice == "3":
        rows = list_contacts(owner, only)
        if not rows:
            return screen(none_yet)
        page, i = 0, 0
        while i < len(rest) and rest[i] == MORE and (page + 1) * PAGE_SIZE < len(rows):
            page, i = page + 1, i + 1
        chunk = rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        first = page * PAGE_SIZE + 1
        if i == len(rest):
            body = format_contacts(chunk, numbered=True, short=True, first=first)
            if (page + 1) * PAGE_SIZE < len(rows):
                body += f"\n{MORE}. More"
            return screen(body + "\n" + PICK_HINT)
        return pick_and_act(owner, caller, chunk, rest[i], rest[i + 1:], first)

    # 4. Delete: 4 -> pick number -> 1 confirm / 2 cancel
    if choice == "4":
        rows = list_contacts(owner, only)[:PAGE_SIZE]
        if not rows:
            return screen(none_yet)
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
        rows = list_contacts(owner, only)
        if not rows:
            return screen(none_yet)
        send_sms_async(caller, "Your contacts:\n" + format_contacts(rows))
        return screen("You will receive your contacts by SMS shortly.")

    return screen(INVALID)


PICK_HINT = "Reply no. or phone no."
QUICK_REPLIES = {"1": "Please call me."}
TYPE_OWN = "2"
NAME_MAX_LEN = 30


def clean_name(raw):
    return " ".join(str(raw or "").split())[:NAME_MAX_LEN]


def local_number(phone):
    return "0" + phone[4:] if phone.startswith("+254") else phone


def signature(owner):
    """Added to every SMS a user sends: it arrives from our sender ID, so say who it's from and how to reply."""
    user = get_user(owner)
    name = user["name"] if user else None
    return f"- {name} ({local_number(owner)})" if name else f"- {local_number(owner)}"


def pick_and_act(owner, caller, rows, selection, rest, first=1):
    """Resolve a contact number shown on the page or a typed phone number, then run the contact menu."""
    selection = selection.strip()
    index = position_of(selection, first, len(rows))
    if index is not None:
        contact = rows[index]
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
    if len(rest) > (3 if action == "2" else 1):
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

    # Send SMS: pick a quick reply (no typing, so the session doesn't time out), or type one
    if action == "2":
        if len(rest) == 1:
            replies = "\n".join(f"{n}. {text}" for n, text in QUICK_REPLIES.items())
            return screen(f"SMS {name}:\n{replies}\n{TYPE_OWN}. Type a message\nOr just type it now")
        choice = rest[1].strip()
        if choice == TYPE_OWN:
            if len(rest) == 2:
                return screen(f"Type your message to {name}:")
            message = rest[2].strip()
        elif len(rest) > 2:
            return screen(INVALID)
        else:
            message = QUICK_REPLIES.get(choice, choice)  # anything else is the message itself
        sign = signature(owner)
        message = message[: SMS_MAX - len(sign) - 1]  # message + signature fit in one SMS
        if not message:
            return screen("Message cannot be empty.")
        send_sms_async(phone, f"{message}\n{sign}")
        return screen(f"Message sent to {name}.")

    # Copy number: USSD can't reach the clipboard, so text it to the user to copy/call/save
    if action == "3":
        send_sms_async(caller, f"{name}: {phone}" if name != phone else phone)
        title = f"{name}\n{phone}" if name != phone else phone
        return screen(f"{title}\nAlso sent to you by SMS: hold it there to copy.")

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
            body = handle(caller, text, session_id, request.values.get("networkCode", ""))
        except Exception:
            log.exception("Error handling USSD request")
            body = end("Something went wrong. Please try again.")

    return Response(body, mimetype="text/plain")


# ---------------------------------------------------------------- mobile app API
# The app logs in with email or phone + a password the user chooses. USSD keeps phone + 4-digit PIN
# (letters are slow on a keypad); both are set when registering in the app.

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
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


def make_token(user):
    # jti identifies this login, so logging out can revoke just this device
    return serializer().dumps({"phone": user["phone"], "v": token_version(user), "jti": secrets.token_urlsafe(12)})


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
        g.jti = jti
        g.phone = user["phone"]
        return view(*args, **kwargs)

    return wrapper


def clean_email(raw):
    """Lower-cased email, "" when none was given, or None when it isn't a valid address."""
    email = str(raw or "").strip().lower()
    if not email:
        return ""
    return email if len(email) <= 254 and EMAIL_RE.match(email) else None


def email_owner(email):
    row = fetch_one("SELECT phone FROM users WHERE email = ?", (email,))
    return row["phone"] if row else None


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


def logged_in(user):
    return jsonify({"token": make_token(user), "phone": user["phone"]})


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
    error = check_password(user, password)
    if error:
        return api_error(error, auth_status(error))
    return logged_in(user)


def active_ussd_sessions(owner):
    since = int(time.time()) - RESUME_SECONDS
    return fetch_all(
        "SELECT caller, saved_at FROM ussd_resume WHERE (owner = ? OR resume_owner = ?) "
        "AND logged_out = 0 AND saved_at >= ? ORDER BY saved_at DESC",
        (owner, owner, since),
    )


def end_ussd_sessions(owner):
    """Log out USSD from the app: the open session ends on its next screen, and no PIN-free continue."""
    execute(
        "UPDATE ussd_resume SET logged_out = 1, resume_owner = NULL, resume_from = NULL, pin_mac = NULL "
        "WHERE (owner = ? OR resume_owner = ?) AND logged_out = 0",
        (owner, owner),
    )


@app.route("/api/ussd/sessions", methods=["GET"])
@require_auth
def api_ussd_sessions():
    """Logged-in USSD sessions on this account (active until RESUME_SECONDS without a screen)."""
    return jsonify({
        "sessions": [
            {
                "phone": local_number(r["caller"]),
                "own_phone": r["caller"] == g.phone,
                "last_active": r["saved_at"],
                "expires_at": r["saved_at"] + RESUME_SECONDS,
            }
            for r in active_ussd_sessions(g.phone)
        ],
        "timeout_seconds": RESUME_SECONDS,
    })


@app.route("/api/ussd/logout", methods=["POST"])
@require_auth
def api_ussd_logout():
    count = len(active_ussd_sessions(g.phone))
    end_ussd_sessions(g.phone)
    return jsonify({"logged_out": count})


@app.route("/api/logout", methods=["POST"])
@require_auth
def api_logout():
    """Log out this device, or everywhere: every app login plus any active USSD login."""
    now = int(time.time())
    if body_json().get("everywhere"):
        execute("UPDATE users SET token_epoch = token_epoch + 1 WHERE phone = ?", (g.phone,))
        end_ussd_sessions(g.phone)
        return jsonify({"logged_out": "everywhere"})
    if g.jti:
        db = get_db()
        db.execute(sql("DELETE FROM revoked_tokens WHERE expires_at < ?"), (now,))
        db.execute(
            sql("INSERT INTO revoked_tokens (jti, expires_at) VALUES (?, ?) ON CONFLICT (jti) DO NOTHING"),
            (g.jti, now + TOKEN_MAX_AGE),
        )
        db.commit()
    return jsonify({"logged_out": "this device"})


@app.route("/api/password/change", methods=["POST"])
@require_auth
def api_change_password():
    body = body_json()
    current, new = str(body.get("current_password", "")), str(body.get("new_password", ""))
    error = password_error(new, body.get("confirm_password", new))
    if error:
        return api_error(error, 400)
    error = check_password(get_user(g.phone), current)
    if error:
        return api_error("Current password is wrong." if error.startswith("Wrong") else error, auth_status(error))
    return logged_in(set_password(g.phone, new))


@app.route("/api/pin/change", methods=["POST"])
@require_auth
def api_change_pin():
    """New USSD PIN, confirmed with the app password (so a forgotten PIN can be replaced)."""
    body = body_json()
    new_pin = str(body.get("new_pin", ""))
    error = pin_error(new_pin, str(body.get("confirm_pin", new_pin)))
    if error:
        return api_error(error, 400)
    error = check_password(get_user(g.phone), str(body.get("password", "")))
    if error:
        return api_error("Password is wrong." if error.startswith("Wrong") else error, auth_status(error))
    return logged_in(update_pin(g.phone, new_pin))


# SMS verification codes (stored in pin_resets) prove the user holds the SIM. They are used to
# register a new number and to reset a forgotten password.

def send_code(phone, purpose):
    """Text a fresh 6-digit code. Returns an error message, or None when sent."""
    now = int(time.time())
    row = fetch_one("SELECT sent_at FROM pin_resets WHERE phone = ?", (phone,))
    if row and now - row["sent_at"] < RESET_RESEND_SECONDS:
        return "A code was just sent. Wait a minute before asking for another."
    code = f"{secrets.randbelow(10 ** 6):06d}"
    db = get_db()
    db.execute(sql("DELETE FROM pin_resets WHERE phone = ?"), (phone,))
    db.execute(
        sql("INSERT INTO pin_resets (phone, code_hash, expires_at, sent_at) VALUES (?, ?, ?, ?)"),
        (phone, generate_password_hash(code), now + RESET_CODE_TTL, now),
    )
    db.commit()
    send_sms_async(phone, f"Your Remote Phone-Book {purpose} code is {code}. It expires in 10 minutes.")
    return None


def use_code(phone, code):
    """Check a code; it can be used once. Returns (error message, status) or None when valid."""
    row = fetch_one("SELECT code_hash, expires_at, attempts FROM pin_resets WHERE phone = ?", (phone,))
    if not row or row["expires_at"] < time.time():
        return "This code has expired. Ask for a new one.", 400
    if not check_password_hash(row["code_hash"], code):
        attempts = row["attempts"] + 1
        if attempts >= MAX_RESET_ATTEMPTS:
            execute("DELETE FROM pin_resets WHERE phone = ?", (phone,))
            return "Too many wrong codes. Ask for a new one.", 429
        execute("UPDATE pin_resets SET attempts = ? WHERE phone = ?", (attempts, phone))
        return "Wrong code.", 401
    execute("DELETE FROM pin_resets WHERE phone = ?", (phone,))
    return None


def registration_error(body, phone):
    """Everything about a registration that can be checked before the SMS code is used up."""
    if not phone:
        return f"Enter your phone number as {LOGIN_PHONE_FORMAT}.", 400
    if get_user(phone):
        return ALREADY_REGISTERED, 409
    email = clean_email(body.get("email"))
    if email is None:
        return "Enter a valid email address, or leave it empty.", 400
    if email and email_owner(email):
        return EMAIL_TAKEN, 409
    return None


@app.route("/api/register/request", methods=["POST"])
def api_register_request():
    """Register, step 1: text a 6-digit code to the new number."""
    body = body_json()
    phone = login_phone(str(body.get("phone", "")))
    failed = registration_error(body, phone)
    if failed:
        return api_error(*failed)
    if sms is None:
        return api_error("SMS service is not available right now.", 503)
    error = send_code(phone, "verification")
    if error:
        return api_error(error, 429)
    return jsonify({"sent": True, "phone": phone})


@app.route("/api/register/confirm", methods=["POST"])
def api_register_confirm():
    """Register, step 2: SMS code + name, optional email, app password and USSD PIN. Logs the user in."""
    if not SECRET_KEY:
        return api_error("App login is not configured on the server.", 503)
    body = body_json()
    phone = login_phone(str(body.get("phone", "")))
    failed = registration_error(body, phone)
    if failed:
        return api_error(*failed)
    name = clean_name(body.get("name"))
    password, pin = str(body.get("password", "")), str(body.get("pin", ""))
    error = (
        (None if name else "Enter your name.")
        or password_error(password, body.get("confirm_password", password))
        or pin_error(pin, str(body.get("confirm_pin", pin)))
    )
    if error:
        return api_error(error, 400)
    failed = use_code(phone, str(body.get("code", "")).strip())
    if failed:
        return api_error(*failed)
    if not set_pin(phone, pin):
        return api_error(ALREADY_REGISTERED, 409)
    execute(
        "UPDATE users SET name = ?, email = ?, password_hash = ? WHERE phone = ?",
        (name, clean_email(body.get("email")) or None, generate_password_hash(password), phone),
    )
    return logged_in(get_user(phone, fresh=True))


@app.route("/api/password/reset/request", methods=["POST"])
def api_reset_request():
    """Forgot password, step 1: text a code to the account's phone (found by email or phone)."""
    user = find_account(body_json().get("login"))
    if not user:
        return api_error(NO_ACCOUNT, 404)
    if sms is None:
        return api_error("SMS service is not available right now.", 503)
    error = send_code(user["phone"], "reset")
    if error:
        return api_error(error, 429)
    return jsonify({"sent": True, "phone": masked(user["phone"])})


@app.route("/api/password/reset/confirm", methods=["POST"])
def api_reset_confirm():
    """Forgot password, step 2: SMS code + new password. Clears the password lockout and logs in."""
    if not SECRET_KEY:
        return api_error("App login is not configured on the server.", 503)
    body = body_json()
    user = find_account(body.get("login"))
    if not user:
        return api_error(NO_ACCOUNT, 404)
    password = str(body.get("new_password", ""))
    error = password_error(password, body.get("confirm_password", password))
    if error:
        return api_error(error, 400)
    failed = use_code(user["phone"], str(body.get("code", "")).strip())
    if failed:
        return api_error(*failed)
    return logged_in(set_password(user["phone"], password))


@app.route("/api/me", methods=["GET"])
@require_auth
def api_me():
    row = fetch_one("SELECT COUNT(*) AS n FROM contacts WHERE owner = ?", (g.phone,))
    user = get_user(g.phone)
    return jsonify({
        "phone": g.phone,
        "contacts": row["n"],
        "show_others": bool(user["show_others"]),
        "name": user["name"],
        "email": user["email"],
    })


@app.route("/api/logins", methods=["GET"])
@require_auth
def api_logins():
    """USSD login history for this account, newest first."""
    try:
        limit = max(1, min(int(request.args.get("limit", 100)), 200))
    except ValueError:
        limit = 100
    rows = fetch_all(
        "SELECT caller, network, success, reason, created_at FROM login_logs WHERE owner = ? "
        "ORDER BY created_at DESC, id DESC LIMIT ?",
        (g.phone, limit),
    )
    return jsonify({
        "logins": [
            {
                "at": r["created_at"],
                "phone": local_number(r["caller"]),
                "own_phone": r["caller"] == g.phone,
                "network": NETWORKS.get(r["network"] or "", r["network"] or "Unknown network"),
                "success": bool(r["success"]),
                "reason": r["reason"],
            }
            for r in rows
        ],
        "keep_days": LOGIN_LOG_DAYS,
    })


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
    })


@app.route("/api/contacts", methods=["POST"])
@require_auth
def api_add_contact():
    body = body_json()
    name = " ".join(str(body.get("name") or "").split())[:60]
    phone = normalize_phone(str(body.get("phone") or ""))
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
    if not fetch_one("SELECT id FROM contacts WHERE id = ? AND owner = ?", (contact_id, g.phone)):
        return api_error("Contact not found.", 404)
    delete_contact(g.phone, contact_id)
    return jsonify({"deleted": contact_id})


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
    """Update show_others, name (signs the user's SMS) and/or email (app login)."""
    body = request.get_json(silent=True) or {}
    if not {"show_others", "name", "email"} & set(body):
        return api_error("Nothing to update.", 400)
    if "email" in body:
        email = clean_email(body["email"])
        if email is None:
            return api_error("Enter a valid email address, or leave it empty.", 400)
        if email and email_owner(email) not in (None, g.phone):
            return api_error(EMAIL_TAKEN, 409)
    if "show_others" in body:
        if not isinstance(body["show_others"], bool):
            return api_error("Expected show_others: true or false.", 400)
        execute("UPDATE users SET show_others = ? WHERE phone = ?", (int(body["show_others"]), g.phone))
    if "name" in body:
        name = clean_name(body["name"])
        if not name:
            return api_error("Enter your name.", 400)
        execute("UPDATE users SET name = ? WHERE phone = ?", (name, g.phone))
    if "email" in body:
        execute("UPDATE users SET email = ? WHERE phone = ?", (email or None, g.phone))
    user = get_user(g.phone, fresh=True)
    return jsonify({"show_others": bool(user["show_others"]), "name": user["name"], "email": user["email"]})


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
