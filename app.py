"""
Phone Book USSD service for Africa's Talking (*384*88041#).

Africa's Talking POSTs to /ussd with form fields:
  sessionId, serviceCode, phoneNumber, networkCode, text
`text` is every input in the session joined by "*" (e.g. "1*John*0712345678").
We reply with plain text starting with "CON " (keep session open) or "END " (close it).

The mobile app and website log in with email or phone number + password and
uploads the device's contacts to /api/contacts/sync.
"""
import os
import re
import unicodedata
import atexit
import base64
import calendar
import datetime
import hashlib
import hmac
import secrets
import smtplib
import ssl
from email.message import EmailMessage
import sqlite3
import threading
import time
import json
import logging
from functools import wraps
from xml.sax.saxutils import quoteattr

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import load_dotenv
from flask import Flask, request, Response, g, jsonify, send_from_directory, session, render_template, redirect, url_for, abort, has_app_context
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
CONTACTS_KEY = os.getenv("CONTACTS_KEY", "")  # encrypts contacts at rest; never stored in the database
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")  # /admin is disabled until this is set
# Email (for verification codes and notifications), sent over SMTP: e.g. Gmail with an app password,
# Brevo, Mailgun or SendGrid SMTP. Nothing is sent until the admin turns the email features on.
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT") or 587)  # 587 = STARTTLS, 465 = TLS from the start
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM") or SMTP_USER

USSD_MAX_LEN = 182  # max characters a USSD screen can show
PAGE_SIZE = 4  # contacts per screen; leaves room for "98. More" and the pick hint
NAME_MAX = 12  # names are shortened on list screens so a full page + the Back/Home/Logout line fits
LIST_SCREEN_LEN = 160  # list screens stay this far below USSD_MAX_LEN: a full 182 is refused by some networks
MORE = "98"  # next page on the contact list (contact numbers skip 98)
SMS_MAX = 160  # one SMS
CALL_TTL = 300  # seconds a requested call stays valid while we ring the caller
SESSION_HISTORY_DAYS = 30  # ended app/USSD sessions shown in the app
RESUME_SECONDS = 40  # a USSD login stays active this long after the last screen (inactivity timeout)


def duration_text(seconds):
    """40 -> "40 seconds", 60 -> "1 minute", 180 -> "3 minutes"."""
    if seconds % 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    minutes = seconds // 60
    return f"{minutes} minute{'s' if minutes != 1 else ''}"

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
app.secret_key = SECRET_KEY or None  # signs the admin login cookie
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=bool(os.getenv("RENDER")),  # https-only cookie on Render; plain http works locally
    PERMANENT_SESSION_LIFETIME=8 * 3600,
)

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


# ---------------------------------------------------------------- feature switches
# Optional features the admin turns on and off at /admin (stored in the settings table, so they
# apply to every server process). All start off, so the prototype behaves as before.

FEATURES = {
    "encrypt_contacts": "Encrypt contacts at rest",
    "email_verification": "Email verification",
    "email_notifications": "Email notifications",
}
SETTINGS_TTL = 5  # seconds a process keeps a setting before reading it again
_settings_cache = {}


def feature_on(name, fresh=False):
    cached = _settings_cache.get(name)
    if cached and not fresh and time.time() - cached[1] < SETTINGS_TTL:
        return cached[0]
    row = fetch_one("SELECT value FROM settings WHERE key = ?", (name,))
    on = bool(row) and row["value"] == "1"
    _settings_cache[name] = (on, time.time())
    return on


def set_feature(name, on):
    execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (name, "1" if on else "0"),
    )
    _settings_cache[name] = (on, time.time())


# ---------------------------------------------------------------- contacts encrypted at rest (optional)
# When the admin turns on "encrypt_contacts", every contact's name and phone number is stored as
# AES-256-GCM ciphertext (random 96-bit nonce per value; the field name is authenticated, so a
# name can't be swapped into a phone column), and existing contacts are encrypted. Turning it off
# decrypts them again. Each row's `enc` flag says which form it is in, so reads work either way.
# The key lives only in the server's environment (CONTACTS_KEY), never in the database.
# Losing CONTACTS_KEY while contacts are encrypted means losing them: keep a copy somewhere safe.

KEYGEN = "python -c \"import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())\""


def _contacts_cipher():
    if not CONTACTS_KEY:
        return None
    try:
        key = base64.urlsafe_b64decode(CONTACTS_KEY)
    except ValueError:
        key = b""
    if len(key) != 32:
        raise SystemExit(f"CONTACTS_KEY must be a 32-byte urlsafe base64 key. Generate one with:\n  {KEYGEN}")
    return AESGCM(key)


CONTACTS_CIPHER = _contacts_cipher()


def seal(value, field):
    nonce = os.urandom(12)
    return base64.b64encode(nonce + CONTACTS_CIPHER.encrypt(nonce, value.encode(), field.encode())).decode()


def unseal(value, field):
    raw = base64.b64decode(value)
    return CONTACTS_CIPHER.decrypt(raw[:12], raw[12:], field.encode()).decode()


def encrypting():
    return bool(CONTACTS_CIPHER) and feature_on("encrypt_contacts", fresh=True)


def stored_contact(name, phone, encrypt=None):
    """(name, phone, enc) as they should be written now: encrypted only while the feature is on."""
    if encrypt if encrypt is not None else encrypting():
        return seal(name, "name"), seal(phone, "phone"), 1
    return name, phone, 0


def open_contacts(rows):
    """Database rows (with their enc flag) -> dicts with plain name and phone."""
    return [
        dict(r, name=unseal(r["name"], "name"), phone=unseal(r["phone"], "phone")) if r["enc"] else dict(r) for r in rows
    ]


def check_contacts_key(db):
    """On startup: encrypted contacts need the key they were encrypted with."""
    row = db.execute("SELECT name FROM contacts WHERE enc = 1 LIMIT 1").fetchone()
    if not row:
        return
    if not CONTACTS_CIPHER:
        raise SystemExit("Some contacts are encrypted: set CONTACTS_KEY to the key they were encrypted with.")
    try:
        unseal(row["name"], "name")
    except (InvalidTag, ValueError):
        raise SystemExit("CONTACTS_KEY does not match the key the stored contacts were encrypted with.")


def convert_contacts(encrypt):
    """Encrypt every plaintext contact (encrypt=True) or decrypt every encrypted one. Returns the count."""
    rows = fetch_all("SELECT id, name, phone, enc FROM contacts WHERE enc = ?", (0 if encrypt else 1,))
    if encrypt:
        new = [(seal(r["name"], "name"), seal(r["phone"], "phone"), 1, r["id"], 0) for r in rows]
    else:
        new = [(r["name"], r["phone"], 0, r["id"], 1) for r in open_contacts(rows)]
    db = get_db()
    for i in range(0, len(new), 500):
        db.cursor().executemany(sql("UPDATE contacts SET name = ?, phone = ?, enc = ? WHERE id = ? AND enc = ?"), new[i:i + 500])
    db.commit()
    return len(new)


# ---------------------------------------------------------------- database

def connect(url=None):
    if DATABASE_URL:
        # prepare_threshold=None: no server-side prepared statements through Neon's PgBouncer pooler
        return psycopg.connect(
            url or DATABASE_URL, row_factory=dict_row, prepare_threshold=None, connect_timeout=DB_CONNECT_TIMEOUT
        )
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


# A USSD screen must answer within ~10 s or the network gives up ("USSD code running..." then an
# error), so a slow database fails fast with "please dial again" instead of hanging the phone.
DB_CONNECT_TIMEOUT = 5
DB_WAIT_TIMEOUT = 5  # waiting for a free pooled connection

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
                max_size=4,  # = gunicorn threads per worker (gunicorn.conf.py)
                timeout=DB_WAIT_TIMEOUT,
                max_idle=240,  # Neon may close idle connections; drop ours first
                check=ConnectionPool.check_connection,  # replace connections Neon closed while idle
                kwargs={"row_factory": dict_row, "prepare_threshold": None, "connect_timeout": DB_CONNECT_TIMEOUT},
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
        """CREATE TABLE IF NOT EXISTS app_sessions (
               jti TEXT PRIMARY KEY,
               owner TEXT NOT NULL,
               device TEXT,
               version TEXT NOT NULL,
               created_at INTEGER NOT NULL,
               last_seen INTEGER NOT NULL,
               ended_at INTEGER,
               end_reason TEXT
           )""",
        f"""CREATE TABLE IF NOT EXISTS ussd_sessions (
               id {id_col},
               owner TEXT NOT NULL,
               caller TEXT NOT NULL,
               network TEXT,
               started_at INTEGER NOT NULL,
               last_active INTEGER NOT NULL,
               ended_at INTEGER,
               end_reason TEXT
           )""",
        "CREATE INDEX IF NOT EXISTS idx_ussd_sessions_owner ON ussd_sessions(owner, started_at)",
        "CREATE INDEX IF NOT EXISTS idx_app_sessions_owner ON app_sessions(owner)",
        """CREATE TABLE IF NOT EXISTS revoked_tokens (
               jti TEXT PRIMARY KEY,
               expires_at INTEGER NOT NULL
           )""",
        """CREATE TABLE IF NOT EXISTS ussd_wrong_pins (
               session_id TEXT NOT NULL,
               mac TEXT NOT NULL,
               created_at INTEGER NOT NULL,
               PRIMARY KEY (session_id, mac)
           )""",
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        f"""CREATE TABLE IF NOT EXISTS complaints (
               id {id_col},
               owner TEXT,
               contact TEXT,
               message TEXT NOT NULL,
               status TEXT NOT NULL DEFAULT 'open',
               reply TEXT,
               ip TEXT,
               created_at INTEGER NOT NULL,
               updated_at INTEGER NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_complaints_status ON complaints(status, created_at)",
        f"""CREATE TABLE IF NOT EXISTS user_emails (
               id {id_col},
               owner TEXT NOT NULL,
               email TEXT NOT NULL UNIQUE,
               verified INTEGER NOT NULL DEFAULT 0,
               created_at INTEGER NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_user_emails_owner ON user_emails(owner)",
        f"""CREATE TABLE IF NOT EXISTS notifications (
               id {id_col},
               owner TEXT NOT NULL,
               kind TEXT NOT NULL,
               title TEXT NOT NULL,
               body TEXT NOT NULL,
               created_at INTEGER NOT NULL,
               read_at INTEGER
           )""",
        "CREATE INDEX IF NOT EXISTS idx_notifications_owner ON notifications(owner, created_at)",
        # Everything sent to users, for the admin: what, to whom, and how it was delivered
        f"""CREATE TABLE IF NOT EXISTS notification_log (
               id {id_col},
               owner TEXT,
               recipient TEXT,
               kind TEXT NOT NULL,
               title TEXT NOT NULL,
               body TEXT NOT NULL,
               inapp_id INTEGER,
               emails TEXT,
               email_status TEXT NOT NULL DEFAULT 'none',
               note TEXT,
               complaint_id INTEGER,
               created_at INTEGER NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_notification_log_time ON notification_log(created_at)",
        """CREATE TABLE IF NOT EXISTS email_codes (
               phone TEXT PRIMARY KEY,
               email TEXT NOT NULL,
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
        add_column(db, "ussd_resume", "login_id", "INTEGER")  # the ussd_sessions row of this login
        add_column(db, "contacts", "enc", "INTEGER NOT NULL DEFAULT 0")  # 1 = name/phone are ciphertext
        add_column(db, "users", "email_verified", "INTEGER NOT NULL DEFAULT 0")
        # Notification choices: the in-app inbox, and which kinds (security alerts, messages from the team)
        add_column(db, "users", "notify_inapp", "INTEGER NOT NULL DEFAULT 1")
        add_column(db, "users", "notify_security", "INTEGER NOT NULL DEFAULT 1")
        add_column(db, "users", "notify_messages", "INTEGER NOT NULL DEFAULT 1")
        add_column(db, "user_emails", "notify", "INTEGER NOT NULL DEFAULT 1")  # this email gets notifications
        # Accounts can have up to MAX_EMAILS emails (user_emails); move the old single users.email there
        db.execute(
            sql(
                "INSERT INTO user_emails (owner, email, verified, created_at) SELECT phone, email, email_verified, ? "
                "FROM users u WHERE email IS NOT NULL AND NOT EXISTS (SELECT 1 FROM user_emails e WHERE e.email = u.email)"
            ),
            (int(time.time()),),
        )
        db.execute("UPDATE users SET email = NULL, email_verified = 0 WHERE email IS NOT NULL")
        check_contacts_key(db)
        db.commit()
    finally:
        db.close()


def add_column(db, table, column, definition):
    if DATABASE_URL:
        db.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")
    elif column not in [row[1] for row in db.execute(f"PRAGMA table_info({table})")]:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def list_contacts(owner, priority_only=False):
    """Decrypted contacts, priority ones first, then by name (sorted here: the database only holds ciphertext)."""
    only = " AND priority = 1" if priority_only else ""
    rows = open_contacts(fetch_all(f"SELECT id, name, phone, priority, enc FROM contacts WHERE owner = ?{only}", (owner,)))
    return sorted(rows, key=lambda r: (-r["priority"], r["name"].lower(), r["id"]))


def search_contacts(owner, query, priority_only=False):
    query = query.lower()
    return [r for r in list_contacts(owner, priority_only) if query in r["name"].lower()][:PAGE_SIZE]


def priority_only(owner):
    """USSD shows only priority contacts when the user turned off "show other contacts" in the app."""
    return not get_user(owner)["show_others"]


def add_contact(owner, name, phone):
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


USER_COLUMNS = (
    "phone, pin_hash, failed_attempts, locked_until, show_others, name, "
    "email, password_hash, pw_failed_attempts, pw_locked_until, token_epoch, email_verified, "
    "notify_inapp, notify_security, notify_messages"
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
    execute(
        "UPDATE ussd_sessions SET ended_at = ?, end_reason = 'PIN changed' WHERE owner = ? AND ended_at IS NULL",
        (int(time.time()), phone),
    )
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


SERVICE_NUMBER_RE = re.compile(r"^\d{3,6}$")  # 999, 112, 1199, 116...

# Free emergency and help lines (Kenya). Calls to these are always dialled from the user's own
# phone, never connected through Africa's Talking.
EMERGENCY_NUMBERS = [  # (number, name for contacts and the app, short name for the USSD list)
    ("999", "Police, fire, ambulance", "Police/Fire/Ambulance"),
    ("112", "Emergency (any mobile)", "Emergency"),
    ("911", "Emergency (any mobile)", "Emergency"),
    ("1199", "Kenya Red Cross", "Red Cross"),
    ("1195", "Gender violence helpline", "GBV helpline"),
    ("116", "Childline Kenya", "Childline"),
]


def is_service_number(phone):
    return bool(SERVICE_NUMBER_RE.match(phone or ""))


def normalize_contact_phone(raw):
    """A contact's number: a full phone number (normalised to +...) or a short service number kept as is."""
    phone = PHONE_JUNK_RE.sub("", raw or "")
    return phone if is_service_number(phone) else normalize_phone(phone)


# Characters that make the network send the whole screen in UCS-2, which fits about half as much
# text: the network then rejects a full screen ("the network is experiencing technical problems").
# Phone contacts often have them (iPhone's ’ in "Mama’s", accents, emoji), so screens use plain text.
PLAIN_SWAPS = str.maketrans({"’": "'", "‘": "'", "‚": ",", "“": '"', "”": '"', "–": "-", "—": "-", "…": "...", "\u00a0": " "})
GSM_SAFE_RE = re.compile(r"[^A-Za-z0-9 \n!\"#%&'()*+,\-./:;<=>?@_£$¥]")


def plain_text(msg):
    msg = unicodedata.normalize("NFKD", msg.translate(PLAIN_SWAPS))
    return re.sub(r"[ \t]+", " ", GSM_SAFE_RE.sub("", msg))


def con(msg):
    return "CON " + plain_text(msg)[: USSD_MAX_LEN - 4]


def end(msg):
    return "END " + plain_text(msg)[: USSD_MAX_LEN - 4]


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


def format_contacts(rows, numbered=False, short=False, first=1, extra=""):
    """Contacts one per line. short: for a menu screen, where the list plus `extra` (the lines shown
    under it) must stay within LIST_SCREEN_LEN, so names are shortened (more on pages with long numbers)."""
    def build(name_max):
        lines = []
        for i, row in enumerate(rows, first):
            prefix = f"{label(i)}. " if numbered else ""
            name = plain_text(row["name"])[:name_max].rstrip() if short else row["name"]
            phone = local_phone(row["phone"]) if short else row["phone"]
            lines.append(f"{prefix}{name} {phone}")
        return "\n".join(lines)

    if not short:
        return build(None)
    room = LIST_SCREEN_LEN - len("CON ") - len("\n" + NAV) - len(extra)
    for name_max in range(NAME_MAX, 3, -1):
        text = build(name_max)
        if len(text) <= room:
            break
    return text


def local_phone(phone):
    """+254712345678 -> 0712345678, shorter on list screens; other countries keep their + code."""
    return "0" + phone[4:] if phone.startswith("+254") and len(phone) == 13 else phone


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
    "1. My contacts\n"
    "2. Emergency numbers\n"
    "3. Add contact\n"
    "4. Search contact\n"
    "5. Delete contact\n"
    "6. SMS me my contacts\n"
    "Or type a name to search\n"
    "000. Log out"
)
# Main menu numbers, in the order shown above
MENU_ACTIONS = {"1": "list", "2": "emergency", "3": "add", "4": "search", "5": "delete", "6": "sms"}
BACK, HOME, LOGOUT = "0", "00", "000"
NAV = "0.Back 00.Home 000.Logout"  # on every screen after login


def screen(msg):
    """A screen inside the menu: always offers Back/Home, so a result or mistake doesn't end the session."""
    room = USSD_MAX_LEN - 4 - len(NAV) - 1
    return con(msg[:room] + "\n" + NAV)


def resolve_nav(inputs):
    """Apply 0 (back one step) and 00 (home) to the inputs typed after logging in.

    Returns None if 000 (log out) was typed.
    """
    path = []
    for value in inputs:
        if value == LOGOUT:
            return None
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
        get_user(phone, fresh=True)  # a second try in this same request must see the new count
        if locked_until:
            notify_event(phone, "pin_lockout", attempts=MAX_PIN_ATTEMPTS, minutes=LOCKOUT_SECONDS // 60)
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
        "SELECT owner, path, saved_at, session_id, offered_in, resume_owner, resume_from, pin_mac, logged_out, login_id "
        "FROM ussd_resume WHERE caller = ?",
        (caller,),
    )


def save_resume(caller, owner, path, session_id, state, mac, login_id):
    keep = state is not None and state["offered_in"] == session_id
    now = int(time.time())
    execute("UPDATE ussd_sessions SET last_active = ? WHERE id = ?", (now, login_id))
    execute(
        "INSERT INTO ussd_resume (caller, owner, path, saved_at, session_id, offered_in, resume_owner, resume_from, pin_mac, "
        "logged_out, login_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?) "
        "ON CONFLICT (caller) DO UPDATE SET owner = excluded.owner, path = excluded.path, saved_at = excluded.saved_at, "
        "session_id = excluded.session_id, offered_in = excluded.offered_in, resume_owner = excluded.resume_owner, "
        "resume_from = excluded.resume_from, pin_mac = excluded.pin_mac, logged_out = 0, login_id = excluded.login_id",
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
            login_id,
        ),
    )


def session_mac(session_id, owner, pin):
    """Proof that this PIN already passed the (deliberately slow) check earlier in this USSD session."""
    if not SECRET_KEY or not session_id:
        return None
    msg = f"{session_id}|{owner}|{pin}".encode()
    return hmac.new(SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


PIN_TRIES = 2  # per USSD session: a wrong first PIN gets one more try
RESET_PIN_HELP = (
    "Reset your PIN in the Remote Phonebook app or at phone-book.odumacorp.com: "
    "log in and tap Change USSD PIN."
)


def wrong_pin_mac(session_id, owner, pin):
    msg = f"wrong|{session_id}|{owner}|{pin}".encode()
    return hmac.new((SECRET_KEY or "dev").encode(), msg, hashlib.sha256).hexdigest()


def known_wrong(session_id, owner, pin):
    """Every screen resends the earlier inputs, so a PIN already rejected in this session is
    remembered rather than checked (and counted toward the lockout) again on each screen."""
    return bool(session_id) and bool(fetch_one(
        "SELECT 1 AS x FROM ussd_wrong_pins WHERE session_id = ? AND mac = ?",
        (session_id, wrong_pin_mac(session_id, owner, pin)),
    ))


def remember_wrong(session_id, owner, pin):
    if not session_id:
        return
    now = int(time.time())
    db = get_db()
    db.execute(sql("DELETE FROM ussd_wrong_pins WHERE created_at < ?"), (now - 3600,))
    db.execute(
        sql("INSERT INTO ussd_wrong_pins (session_id, mac, created_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING"),
        (session_id, wrong_pin_mac(session_id, owner, pin), now),
    )
    db.commit()


def authenticate(owner, inputs, session_id, state, caller, network):
    """PIN entry with a second try. `inputs` starts at the first PIN.

    Returns (mac, inputs after the PIN that worked, None), or (None, None, a screen to show).
    """
    for attempt in range(PIN_TRIES):
        if len(inputs) <= attempt:
            return None, None, con(f"Wrong PIN. You have {PIN_TRIES - attempt} more try. Enter your PIN:")
        pin = inputs[attempt]
        error = "Wrong phone number or PIN." if known_wrong(session_id, owner, pin) else None
        if error is None:
            mac, error = login(owner, pin, session_id, state, caller, network)
            if error is None:
                return mac, inputs[attempt + 1:], None
            if not error.startswith("Too many"):
                remember_wrong(session_id, owner, pin)
        if error.startswith("Too many"):
            return None, None, end(f"{error} {RESET_PIN_HELP}")
    return None, None, end(f"Wrong PIN again. {RESET_PIN_HELP}")


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
    if success and not resumed and caller != owner:
        notify_event(owner, "ussd_other_phone", caller=masked(caller))
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


# ---- USSD session history: one ussd_sessions row per login (a resume continues the same one)

def start_ussd_login(owner, caller, network, state):
    """A fresh phone-number + PIN login. Any login still open on this phone is ended first."""
    if state and state["login_id"]:
        end_ussd_login(state["login_id"], "new login")
    now = int(time.time())
    db = get_db()
    cur = db.cursor()
    query = (
        "INSERT INTO ussd_sessions (owner, caller, network, started_at, last_active) VALUES (?, ?, ?, ?, ?)"
        + (" RETURNING id" if DATABASE_URL else "")
    )
    cur.execute(sql(query), (owner, caller, network or None, now, now))
    login_id = cur.fetchone()["id"] if DATABASE_URL else cur.lastrowid
    db.commit()
    return login_id


def end_ussd_login(login_id, reason):
    if login_id:
        execute(
            "UPDATE ussd_sessions SET ended_at = ?, end_reason = ? WHERE id = ? AND ended_at IS NULL",
            (int(time.time()), reason, login_id),
        )


def close_idle_ussd_logins():
    """Logins idle past RESUME_SECONDS ended when they expired, not when we noticed."""
    execute(
        "UPDATE ussd_sessions SET ended_at = last_active + ?, end_reason = 'timed out' "
        "WHERE ended_at IS NULL AND last_active < ?",
        (RESUME_SECONDS, int(time.time()) - RESUME_SECONDS),
    )


def current_login(owner, caller, network, session_id, state):
    """The ussd_sessions row this request belongs to: the same one while this network session
    (or a resume of it) goes on, otherwise a new login."""
    if state and state["login_id"] and state["owner"] == owner and session_id in (state["session_id"], state["offered_in"]):
        return state["login_id"]
    return start_ussd_login(owner, caller, network, state)


def run_menu(owner, caller, path, session_id, state, mac, login_id):
    """Show a menu screen and remember it for resuming (never a path that just ran an action)."""
    if path is None:  # 000: log out
        execute("DELETE FROM ussd_resume WHERE caller = ?", (caller,))
        end_ussd_login(login_id, "logged out on USSD")
        return end("You are logged out of Remote Phone-Book. Dial again to log in.")
    g.action_done = False
    body = menu(owner, caller, path)
    if body.startswith("CON "):
        save_resume(caller, owner, [] if g.action_done else path, session_id, state, mac, login_id)
    else:
        execute("DELETE FROM ussd_resume WHERE caller = ?", (caller,))
        end_ussd_login(login_id, "session ended")
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
        close_idle_ussd_logins()
        execute("DELETE FROM ussd_resume WHERE saved_at < ?", (int(time.time()) - RESUME_SECONDS,))
        if expired:
            return con(
                f"Your session expired after {duration_text(RESUME_SECONDS)} of inactivity. "
                "You were logged out for security.\nEnter your phone number:"
            )
        return con("Welcome to Remote Phone-Book\nEnter your phone number:")

    if state and state["offered_in"] == session_id and trusted_resume(state, caller, offered=True):
        # Own phone, still within the inactivity window: continue / main menu (each needs the PIN) / log out
        if parts[0] in ("1", "2"):
            owner = state["resume_owner"]
            if len(parts) == 1:
                return con(f"Enter PIN for {masked(owner)}:")
            mac, rest, screen_ = authenticate(owner, parts[1:], session_id, state, caller, network)
            if screen_:
                return screen_
            start = json.loads(state["resume_from"]) if parts[0] == "1" else []
            login_id = current_login(owner, caller, network, session_id, state)
            return run_menu(owner, caller, resolve_nav(start + rest), session_id, state, mac, login_id)
        if parts[0] != BACK:
            return end("Invalid choice. Please dial again.")
        # Log out: end the active login but keep offered_in, so this session's later inputs
        # ("0*<phone>*<PIN>...") are still read as "start over" below
        end_ussd_login(state["login_id"], "logged out on USSD")
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
            mac, rest, screen_ = authenticate(owner, parts, session_id, state, caller, network)
            if screen_:
                return screen_
            path = resolve_nav(json.loads(state["resume_from"]) + rest)
            login_id = current_login(owner, caller, network, session_id, state)
            return run_menu(owner, caller, path, session_id, state, mac, login_id)
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
    mac, rest, screen_ = authenticate(owner, parts[1:], session_id, state, caller, network)
    if screen_:
        return screen_  # second try, or (after two wrong PINs) how to reset it in the app/website
    login_id = current_login(owner, caller, network, session_id, state)
    return run_menu(owner, caller, resolve_nav(rest), session_id, state, mac, login_id)


# ---------------------------------------------------------------- menu logic
# Every request replays the whole path, so actions (save, delete, send, call) only run when
# the path ends exactly at them; anything typed after a result is rejected, not re-run.

def menu(owner, caller, parts):
    if not parts:
        return con(MAIN_MENU)

    choice, rest = MENU_ACTIONS.get(parts[0]), parts[1:]
    if parts[0].strip() and not parts[0].strip().isdigit():
        return menu(owner, caller, ["4"] + parts)  # typed a name on the main menu: search straight away
    only = priority_only(owner)
    none_yet = NO_PRIORITY if only else "You have no contacts yet."

    # 3. Add contact: 3 -> name -> phone
    if choice == "add":
        if len(rest) == 0:
            return screen("Enter contact name:")
        name = rest[0].strip()
        if not name:
            return screen("Name cannot be empty.")
        if len(rest) == 1:
            return screen(f"Enter phone number for {name}:")
        if len(rest) > 2:
            return screen(INVALID)
        phone = normalize_contact_phone(rest[1])
        if not phone:
            return screen("Invalid phone number.")
        add_contact(owner, name, phone)
        return screen(f"Saved {name} - {phone}")

    # 4. Search: 4 -> query -> pick contact -> action
    if choice == "search":
        if len(rest) == 0:
            return screen("Enter name to search:")
        query = rest[0].strip()
        rows = search_contacts(owner, query, only)
        if not rows:
            return screen(f"No contacts match '{query}'.")
        if len(rest) == 1:
            return screen(format_contacts(rows, numbered=True, short=True, extra="\n" + PICK_HINT) + "\n" + PICK_HINT)
        return pick_and_act(owner, caller, rows, rest[1], rest[2:])

    # 1. List, paginated: 1 -> 98 (more) -> 98 ... -> pick contact -> action
    if choice == "list":
        rows = list_contacts(owner, only)
        if not rows:
            return screen(none_yet)
        page, i = 0, 0
        while i < len(rest) and rest[i] == MORE and (page + 1) * PAGE_SIZE < len(rows):
            page, i = page + 1, i + 1
        chunk = rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        first = page * PAGE_SIZE + 1
        if i == len(rest):
            more = f"\n{MORE}. More" if (page + 1) * PAGE_SIZE < len(rows) else ""
            body = format_contacts(chunk, numbered=True, short=True, first=first, extra=more + "\n" + PICK_HINT)
            return screen(body + more + "\n" + PICK_HINT)
        return pick_and_act(owner, caller, chunk, rest[i], rest[i + 1:], first)

    # 5. Delete: 5 -> pick number -> 1 confirm / 2 cancel
    if choice == "delete":
        rows = list_contacts(owner, only)[:PAGE_SIZE]
        if not rows:
            return screen(none_yet)
        if len(rest) == 0:
            title = "Select contact to delete:\n"
            return screen(title + format_contacts(rows, numbered=True, short=True, extra=title))
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

    # 6. SMS full phone book to the phone in use
    if choice == "sms":
        if rest:
            return screen(INVALID)
        if sms is None:
            return screen("SMS service is not available right now.")
        rows = list_contacts(owner, only)
        if not rows:
            return screen(none_yet)
        send_sms_async(caller, "Your contacts:\n" + format_contacts(rows))
        return screen("You will receive your contacts by SMS shortly.")

    # 2. Emergency numbers: 2 -> pick -> 1 call now (dial it yourself) / 2 add to contacts
    if choice == "emergency":
        if len(rest) == 0:
            lines = "\n".join(f"{i}. {num} {short}" for i, (num, _label, short) in enumerate(EMERGENCY_NUMBERS, 1))
            return screen(f"Emergency (free):\n{lines}")
        if not rest[0].isdigit() or not 1 <= int(rest[0]) <= len(EMERGENCY_NUMBERS):
            return screen(INVALID)
        number, label, _short = EMERGENCY_NUMBERS[int(rest[0]) - 1]
        if len(rest) == 1:
            return screen(f"{number} {label}\n1. Call now\n2. Add to my contacts")
        if len(rest) > 2:
            return screen(INVALID)
        if rest[1] == "1":
            return end(dial_yourself(number))
        if rest[1] == "2":
            existing = find_contact_by_phone(owner, number)
            if existing:
                return screen(f"{number} is already in your contacts as {existing['name']}.")
            add_contact(owner, label, number)
            return screen(f"Saved {label} - {number}")
        return screen(INVALID)

    return screen(INVALID)


def dial_yourself(number):
    # Ending the USSD session frees the phone to dial straight away
    return f"Dial {number} now from your phone. It is free."


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
        phone = normalize_contact_phone(selection)
        if not phone:
            return screen("Enter a number from the list or a phone number.")
        contact = find_contact_by_phone(owner, phone) or {"name": phone, "phone": phone}
    return contact_menu(owner, caller, contact, rest)


def calling_available():
    return voice is not None and bool(AT_VOICE_NUMBER)


def contact_menu(owner, caller, contact, rest):
    name, phone = contact["name"], contact["phone"]
    if is_service_number(phone):
        # 999, 1199...: never connected through Africa's Talking, and they can't receive SMS
        if len(rest) == 0:
            title = name if name == phone else f"{name}\n{phone}"
            return screen(f"{title}\n1. Call\n3. Copy number")
        if rest == ["1"]:
            return end(dial_yourself(phone))
        if rest[0] != "3" or len(rest) > 1:
            return screen(INVALID)
    if len(rest) == 0:
        title = name if name == phone else f"{name}\n{phone}"
        return screen(f"{title}\n1. Call\n2. Send SMS\n3. Copy number")

    action = rest[0]
    if action == "1" and not calling_available():
        # No voice number set up yet: say so, and offer the options that do work. What they
        # pick next is read as a choice on this contact's menu.
        if len(rest) == 1:
            return screen("Calling isn't available yet.\n2. Send SMS\n3. Copy number")
        return contact_menu(owner, caller, contact, rest[1:])
    if len(rest) > (3 if action == "2" else 1):
        return screen(INVALID)

    # Call: Africa's Talking rings the caller, then /voice bridges them to the contact
    if action == "1":
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
            body = end("Remote Phone-Book is busy right now. Please dial again.")

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


def clean_email(raw):
    """Lower-cased email, "" when none was given, or None when it isn't a valid address."""
    email = str(raw or "").strip().lower()
    if not email:
        return ""
    return email if len(email) <= 254 and EMAIL_RE.match(email) else None


MAX_EMAILS = 3  # emails per account; each can be used to log in


def email_owner(email):
    row = fetch_one("SELECT owner FROM user_emails WHERE email = ?", (email,))
    return row["owner"] if row else None


def user_emails(owner):
    return fetch_all("SELECT id, email, verified, notify FROM user_emails WHERE owner = ? ORDER BY created_at, id", (owner,))


def emails_json(owner):
    return [
        {"id": r["id"], "email": r["email"], "verified": bool(r["verified"]), "notify": bool(r["notify"])}
        for r in user_emails(owner)
    ]


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


def active_ussd_sessions(owner):
    since = int(time.time()) - RESUME_SECONDS
    return fetch_all(
        "SELECT caller, saved_at FROM ussd_resume WHERE (owner = ? OR resume_owner = ?) "
        "AND logged_out = 0 AND saved_at >= ? ORDER BY saved_at DESC",
        (owner, owner, since),
    )


def end_ussd_sessions(owner, caller=None, reason="logged out from the app"):
    """Log out USSD from the app: the open session ends on its next screen, and no PIN-free continue.

    All of the account's USSD sessions, or only the one on `caller`.
    """
    only = " AND caller = ?" if caller else ""
    extra = (caller,) if caller else ()
    execute(
        "UPDATE ussd_resume SET logged_out = 1, resume_owner = NULL, resume_from = NULL, pin_mac = NULL "
        f"WHERE (owner = ? OR resume_owner = ?) AND logged_out = 0{only}",
        (owner, owner) + extra,
    )
    execute(
        f"UPDATE ussd_sessions SET ended_at = ?, end_reason = ? WHERE owner = ? AND ended_at IS NULL{only}",
        (int(time.time()), reason, owner) + extra,
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
    """End all of this account's USSD sessions."""
    close_idle_ussd_logins()
    count = len(active_ussd_sessions(g.phone))
    end_ussd_sessions(g.phone)
    return jsonify({"logged_out": count})


def session_times(started, ended, now):
    return {
        "logged_in": started,
        "logged_out": ended,
        "duration_seconds": (ended or now) - started,
        "active": ended is None,
    }


@app.route("/api/sessions", methods=["GET"])
@require_auth
def api_sessions():
    """App devices and USSD logins on this account: active ones, then ended ones from the last
    SESSION_HISTORY_DAYS, with login time, logout time and duration."""
    now = int(time.time())
    since = now - SESSION_HISTORY_DAYS * 86400
    version = token_version(get_user(g.phone))
    # Expired or out-of-date app sessions end lazily (their tokens already stopped working)
    execute(
        "UPDATE app_sessions SET ended_at = created_at + ?, end_reason = 'expired' WHERE owner = ? AND ended_at IS NULL AND created_at < ?",
        (TOKEN_MAX_AGE, g.phone, now - TOKEN_MAX_AGE),
    )
    execute(
        "UPDATE app_sessions SET ended_at = ?, end_reason = 'password or PIN changed' "
        "WHERE owner = ? AND ended_at IS NULL AND version <> ?",
        (now, g.phone, version),
    )
    close_idle_ussd_logins()
    app_rows = fetch_all(
        "SELECT jti, device, created_at, last_seen, ended_at, end_reason FROM app_sessions "
        "WHERE owner = ? AND (ended_at IS NULL OR ended_at >= ?) ORDER BY created_at DESC LIMIT 50",
        (g.phone, since),
    )
    ussd_rows = fetch_all(
        "SELECT id, caller, network, started_at, last_active, ended_at, end_reason FROM ussd_sessions "
        "WHERE owner = ? AND (ended_at IS NULL OR ended_at >= ?) ORDER BY started_at DESC LIMIT 50",
        (g.phone, since),
    )
    active_first = lambda item: not item["active"]
    return jsonify({
        "app": sorted(
            (
                {
                    "id": r["jti"],
                    "device": r["device"],
                    "current": r["jti"] == g.jti,
                    "last_active": r["last_seen"],
                    "end_reason": r["end_reason"],
                    **session_times(r["created_at"], r["ended_at"], now),
                }
                for r in app_rows
            ),
            key=active_first,
        ),
        "ussd": sorted(
            (
                {
                    "id": r["id"],
                    "phone": local_number(r["caller"]),
                    "own_phone": r["caller"] == g.phone,
                    "network": NETWORKS.get(r["network"] or "", r["network"] or "Unknown network"),
                    "last_active": r["last_active"],
                    "expires_at": None if r["ended_at"] else r["last_active"] + RESUME_SECONDS,
                    "end_reason": r["end_reason"],
                    **session_times(r["started_at"], r["ended_at"], now),
                }
                for r in ussd_rows
            ),
            key=active_first,
        ),
        "ussd_timeout_seconds": RESUME_SECONDS,
        "history_days": SESSION_HISTORY_DAYS,
    })


@app.route("/api/sessions/app/<session_id>/logout", methods=["POST"])
@require_auth
def api_logout_app_session(session_id):
    row = fetch_one("SELECT ended_at FROM app_sessions WHERE jti = ? AND owner = ?", (session_id, g.phone))
    if not row or row["ended_at"]:
        return api_error("That session has already ended.", 404)
    current = session_id == g.jti
    revoke_app_session(g.phone, session_id, "logged out" if current else "logged out from another device")
    return jsonify({"logged_out": session_id, "current": current})


@app.route("/api/sessions/ussd/<int:login_id>/logout", methods=["POST"])
@require_auth
def api_logout_ussd_session(login_id):
    close_idle_ussd_logins()
    row = fetch_one("SELECT caller, ended_at FROM ussd_sessions WHERE id = ? AND owner = ?", (login_id, g.phone))
    if not row or row["ended_at"]:
        return api_error("That USSD session has already ended.", 404)
    end_ussd_sessions(g.phone, row["caller"])
    return jsonify({"logged_out": login_id})


@app.route("/api/logout", methods=["POST"])
@require_auth
def api_logout():
    """Log out this device, or everywhere: every app login plus any active USSD login."""
    if body_json().get("everywhere"):
        execute("UPDATE users SET token_epoch = token_epoch + 1 WHERE phone = ?", (g.phone,))
        execute(
            "UPDATE app_sessions SET ended_at = ?, end_reason = 'logged out everywhere' WHERE owner = ? AND ended_at IS NULL",
            (int(time.time()), g.phone),
        )
        end_ussd_sessions(g.phone, reason="logged out everywhere")
        return jsonify({"logged_out": "everywhere"})
    if g.jti:
        revoke_app_session(g.phone, g.jti, "logged out")
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
    notify_event(g.phone, "password_changed")
    return logged_in(set_password(g.phone, new), same_session=True)


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
    notify_event(g.phone, "pin_changed")
    return logged_in(update_pin(g.phone, new_pin), same_session=True)


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
    execute("UPDATE users SET name = ?, password_hash = ? WHERE phone = ?", (name, generate_password_hash(password), phone))
    email = clean_email(body.get("email"))
    if email:
        execute("INSERT INTO user_emails (owner, email, created_at) VALUES (?, ?, ?)", (phone, email, int(time.time())))
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
    notify_event(user["phone"], "password_reset")
    return logged_in(set_password(user["phone"], password))


@app.route("/api/me", methods=["GET"])
@require_auth
def api_me():
    row = fetch_one("SELECT COUNT(*) AS n FROM contacts WHERE owner = ?", (g.phone,))
    user = get_user(g.phone)
    emails = emails_json(g.phone)
    return jsonify({
        "phone": g.phone,
        "contacts": row["n"],
        "show_others": bool(user["show_others"]),
        "name": user["name"],
        "email": emails[0]["email"] if emails else None,  # the first one (older app versions)
        "emails": emails,
        "max_emails": MAX_EMAILS,
        "features": {"email_verification": feature_on("email_verification") and email_configured()},
        "unread_notifications": unread_count(g.phone),
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


@app.route("/api/emergency", methods=["GET"])
@require_auth
def api_emergency():
    saved = {r["phone"] for r in list_contacts(g.phone)}
    return jsonify({"numbers": [{"number": n, "label": label, "saved": n in saved} for n, label, _short in EMERGENCY_NUMBERS]})


@app.route("/api/contacts", methods=["POST"])
@require_auth
def api_add_contact():
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
    if not fetch_one("SELECT id FROM contacts WHERE id = ? AND owner = ?", (contact_id, g.phone)):
        return api_error("Contact not found.", 404)
    delete_contact(g.phone, contact_id)
    return jsonify({"deleted": contact_id})


@app.route("/api/contacts/delete", methods=["POST"])
@require_auth
def api_delete_contacts():
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
    """Update show_others and/or name (signs the user's SMS). Emails: /api/emails."""
    body = request.get_json(silent=True) or {}
    if not {"show_others", "name"} & set(body):
        return api_error("Nothing to update." + (" Emails are managed with /api/emails." if "email" in body else ""), 400)
    if "show_others" in body:
        if not isinstance(body["show_others"], bool):
            return api_error("Expected show_others: true or false.", 400)
        execute("UPDATE users SET show_others = ? WHERE phone = ?", (int(body["show_others"]), g.phone))
    if "name" in body:
        name = clean_name(body["name"])
        if not name:
            return api_error("Enter your name.", 400)
        execute("UPDATE users SET name = ? WHERE phone = ?", (name, g.phone))
    user = get_user(g.phone, fresh=True)
    return jsonify({"show_others": bool(user["show_others"]), "name": user["name"]})


@app.route("/api/contacts/sync", methods=["POST"])
@require_auth
def api_sync():
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


@app.route("/health", methods=["GET"])
def health():
    return "Phone Book USSD service is running"


# ---------------------------------------------------------------- email (optional)
# Verification codes and security notifications by email. Both are off until the admin turns
# them on at /admin, and both need SMTP_* settings, so the prototype never depends on email.

USSD_CODE_TEXT = "*384*88041#"
EMAIL_CODE_TTL = 15 * 60


def email_configured():
    return bool(SMTP_HOST and EMAIL_FROM)


def send_email(to, subject, body):
    """Send one email now; raises on failure (the admin test uses this to show the error)."""
    msg = EmailMessage()
    if has_app_context():
        sender, signature = email_part("sender_name"), email_part("signature")
    else:  # background thread: read the admin's settings in a context of its own
        with app.app_context():
            sender, signature = email_part("sender_name"), email_part("signature")
    sender = sender.replace('"', "").replace("<", "").replace(">", "")[:60]
    msg["From"] = f'"{sender}" <{EMAIL_FROM}>'
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body + (f"\n\n{signature}" if signature.strip() else ""))
    context = ssl.create_default_context()
    if SMTP_PORT == 465:
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15, context=context)
    else:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
        server.starttls(context=context)
    with server:
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)


def send_email_async(to, subject, body):
    """Email in the background, so USSD screens and app requests never wait on the mail server."""
    def _send():
        try:
            send_email(to, subject, body)
            log.info("email sent to %s: %s", to, subject)
        except Exception:
            log.exception("email to %s failed", to)

    threading.Thread(target=_send, daemon=True).start()


NOTIFICATION_DAYS = 90  # the in-app inbox keeps this long


def notification_emails(owner):
    """The account's emails that get notifications: switched on by the user (and verified, while
    the admin has email verification on)."""
    if not email_configured():
        return []
    verified_only = feature_on("email_verification")
    return [r["email"] for r in user_emails(owner) if r["notify"] and (r["verified"] or not verified_only)]


# ---------------------------------------------------------------- message templates
# The admin edits every notification and email at /admin/templates: on/off, subject and text with
# {placeholders}. Edits are stored in the settings table ("tpl:<key>:subject" ...); empty = default.

COMMON_VARS = {"name": "the user's name", "phone": "their phone number", "app": "Remote Phonebook", "ussd_code": USSD_CODE_TEXT}
TEMPLATES = {
    "new_login": {
        "label": "New app login", "kind": "security", "vars": {"device": "the phone or browser"},
        "subject": "New login to your {app} account",
        "body": "Hi {name},\n\nYour account was just logged into from {device}.\n\nIf this wasn't you, change your password in the app.",
    },
    "password_changed": {
        "label": "Password changed", "kind": "security", "vars": {},
        "subject": "Your password was changed",
        "body": "Hi {name},\n\nYour {app} password was just changed, and your other devices were logged out.\n\n"
                "If this wasn't you, reset your password with Forgot password in the app.",
    },
    "password_reset": {
        "label": "Password reset", "kind": "security", "vars": {},
        "subject": "Your password was reset",
        "body": "Hi {name},\n\nYour {app} password was just reset with a code sent to your phone.\n\n"
                "If this wasn't you, reset it again and contact support.",
    },
    "pin_changed": {
        "label": "USSD PIN changed", "kind": "security", "vars": {},
        "subject": "Your USSD PIN was changed",
        "body": "Hi {name},\n\nThe PIN for {ussd_code} on your {app} account was just changed.\n\n"
                "If this wasn't you, change your password and PIN in the app.",
    },
    "ussd_other_phone": {
        "label": "USSD login from another phone", "kind": "security", "vars": {"caller": "the phone that dialled"},
        "subject": "USSD login from another phone",
        "body": "Hi {name},\n\nYour {app} account was opened on USSD from {caller}.\n\n"
                "If this wasn't you, end the session in the app (Sessions) and change your USSD PIN.",
    },
    "pin_lockout": {
        "label": "USSD locked after wrong PINs", "kind": "security", "vars": {"attempts": "wrong PINs", "minutes": "lockout"},
        "subject": "USSD login locked after wrong PINs",
        "body": "Hi {name},\n\n{attempts} wrong PINs were entered for your account on USSD, so USSD login is locked "
                "for {minutes} minutes.\n\nIf this wasn't you, change your USSD PIN in the app.",
    },
    "report_reply": {
        "label": "Reply to a report", "kind": "messages",
        "vars": {"report_id": "report number", "status": "resolved / answered", "report": "what they wrote", "reply": "your reply"},
        "subject": "Your report #{report_id} was {status}",
        "body": "Hi {name},\n\nYou wrote:\n{report}\n\nOur reply:\n{reply}\n\nYou can also see it in the app under Report a problem.",
    },
    "email_code": {
        "label": "Email verification code (email only)", "kind": "email", "vars": {"code": "the 6-digit code", "minutes": "valid for", "email": "the address"},
        "subject": "Your {app} verification code",
        "body": "Your code is {code}. Enter it in the {app} app to verify {email}. It expires in {minutes} minutes.",
    },
}
EMAIL_PARTS = {  # editable parts of every email
    "sender_name": ("Sender name", "Remote Phonebook"),
    "notify_footer": ("Footer on notification emails", "Choose where you get notifications in the app: Notifications."),
    "signature": ("Signature on every email", "- Remote Phonebook (Oduma Corp)"),
}
SAMPLE_VARS = {
    "name": "Jane", "phone": "0712345678", "device": "Pixel 8", "caller": "0712***678", "attempts": "5", "minutes": "15",
    "report_id": "12", "status": "resolved", "report": "I can't add a contact", "reply": "Fixed, please try again.",
    "code": "123456", "email": "jane@example.com",
}
_text_cache = {}


def setting_text(key):
    """A stored setting ("" when not set), cached briefly like the feature switches."""
    cached = _text_cache.get(key)
    if cached and time.time() - cached[1] < SETTINGS_TTL:
        return cached[0]
    row = fetch_one("SELECT value FROM settings WHERE key = ?", (key,))
    value = row["value"] if row else ""
    _text_cache[key] = (value, time.time())
    return value


def set_setting_text(key, value):
    if value:
        execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    else:
        execute("DELETE FROM settings WHERE key = ?", (key,))
    _text_cache[key] = (value, time.time())


def template_part(key, part):
    """The admin's version of a template's subject/body, or the default."""
    return setting_text(f"tpl:{key}:{part}") or TEMPLATES[key][part]


def template_on(key):
    return setting_text(f"tpl:{key}:enabled") != "0"


NONE = "__none__"  # stored when the admin empties an email part on purpose


def email_part(key):
    value = setting_text(f"email:{key}")
    return "" if value == NONE else value or EMAIL_PARTS[key][1]


def fill(text, values):
    """Replace {placeholders} we know; anything else in braces is left as typed."""
    return re.sub(r"\{(\w+)\}", lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), text)


def render_message(key, values):
    values = {"app": "Remote Phonebook", "ussd_code": USSD_CODE_TEXT, **values}
    return fill(template_part(key, "subject"), values), fill(template_part(key, "body"), values)


def notify_event(owner, key, complaint_id=None, **values):
    """Send one of the admin-editable notifications to an account."""
    try:
        user = get_user(owner)
        if not user:
            return []
        values = {"name": user["name"] or "there", "phone": local_number(owner), **values}
        subject, body = render_message(key, values)
        log_kind = "reply" if key == "report_reply" else "security" if TEMPLATES[key]["kind"] == "security" else "message"
        if not template_on(key):
            log_notification(owner, owner, log_kind, subject, body, note="Not sent: this notification is turned off in admin",
                             complaint_id=complaint_id)
            return []
        return notify(owner, subject, body, kind=TEMPLATES[key]["kind"], log_kind=log_kind, complaint_id=complaint_id)
    except Exception:
        log.exception("notification %s for %s failed", key, owner)
        return []


NOTIFICATION_LOG_DAYS = 90
LOG_KINDS = {"security": "Security alert", "message": "Message from the team", "reply": "Reply to a report"}


def insert_id(query, params):
    """INSERT and return the new row's id (Postgres RETURNING / SQLite lastrowid)."""
    db = get_db()
    cur = db.cursor()
    cur.execute(sql(query + (" RETURNING id" if DATABASE_URL else "")), params)
    new_id = cur.fetchone()["id"] if DATABASE_URL else cur.lastrowid
    db.commit()
    return new_id


def log_notification(owner, recipient, kind, title, body, inapp_id=None, emails=(), note=None, complaint_id=None):
    now = int(time.time())
    log_id = insert_id(
        "INSERT INTO notification_log (owner, recipient, kind, title, body, inapp_id, emails, email_status, note, "
        "complaint_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (owner, recipient, kind, title[:200], body[:2000], inapp_id, ", ".join(emails) or None,
         "sending" if emails else "none", note, complaint_id, now),
    )
    execute("DELETE FROM notification_log WHERE created_at < ?", (now - NOTIFICATION_LOG_DAYS * 86400,))
    return log_id


def notify(owner, subject, body, kind="security", log_kind=None, complaint_id=None):
    """Tell the account holder something, where they chose to hear it.

    kind "security" (logins, password/PIN changes, lockouts) or "messages" (replies to reports and
    messages from the team). Each goes to the in-app inbox and the user's chosen emails, unless the
    user turned that kind off. Security emails also need the admin's "Email notifications" switch;
    messages from the team are emailed whenever email is configured. Every one is logged for the
    admin (log_kind: security / message / reply). Returns the emails it went to.
    """
    log_kind = log_kind or ("security" if kind == "security" else "message")
    try:
        user = get_user(owner)
        if not user:
            return []
        if not user["notify_security" if kind == "security" else "notify_messages"]:
            what = "security alerts" if kind == "security" else "messages from the team"
            log_notification(owner, owner, log_kind, subject, body, note=f"Not delivered: the user turned off {what}",
                             complaint_id=complaint_id)
            return []
        now, inapp_id, notes = int(time.time()), None, []
        if user["notify_inapp"]:
            inapp_id = insert_id(
                "INSERT INTO notifications (owner, kind, title, body, created_at) VALUES (?, ?, ?, ?, ?)",
                (owner, kind, subject[:200], body[:2000], now),
            )
            execute("DELETE FROM notifications WHERE owner = ? AND created_at < ?", (owner, now - NOTIFICATION_DAYS * 86400))
        else:
            notes.append("in-app list turned off by the user")
        emails = []
        if kind == "security" and not feature_on("email_notifications"):
            notes.append("security emails are switched off in admin")
        elif not email_configured():
            notes.append("email isn't configured")
        else:
            emails = notification_emails(owner)
            if not emails:
                notes.append("no email chosen" + (" or verified" if feature_on("email_verification") else ""))
        footer = email_part("notify_footer")
        log_id = log_notification(owner, owner, log_kind, subject, body, inapp_id, emails,
                                  "; ".join(notes).capitalize() or None, complaint_id)
        send_emails_async(
            [(to, subject, body + (f"\n\n{footer}" if footer.strip() else "")) for to in emails], log_id
        )
        return emails
    except Exception:
        log.exception("notification for %s failed", owner)  # never break a login over a notification
        return []


def send_emails_async(messages, log_id=None):
    """Several emails, one after another in one background thread; the outcome goes in the admin log."""
    if not messages:
        return

    def _send():
        failures = []
        for to, subject, body in messages:
            try:
                send_email(to, subject, body)
                log.info("email sent to %s: %s", to, subject)
            except Exception as e:
                log.exception("email to %s failed", to)
                failures.append(f"{to}: {type(e).__name__}: {e}"[:300])
        if log_id is None:
            return
        status = "sent" if not failures else "failed" if len(failures) == len(messages) else "partial"
        try:
            with app.app_context():
                execute(
                    "UPDATE notification_log SET email_status = ?, note = COALESCE(note || '; ', '') || ? WHERE id = ?"
                    if failures else "UPDATE notification_log SET email_status = ? WHERE id = ?",
                    (status, "; ".join(failures), log_id) if failures else (status, log_id),
                )
        except Exception:
            log.exception("could not record email status")

    threading.Thread(target=_send, daemon=True).start()


def notifications_json(owner):
    rows = fetch_all(
        "SELECT id, kind, title, body, created_at, read_at FROM notifications WHERE owner = ? "
        "ORDER BY created_at DESC, id DESC LIMIT 100",
        (owner,),
    )
    return [
        {"id": r["id"], "kind": r["kind"], "title": r["title"], "body": r["body"], "created_at": r["created_at"],
         "read": r["read_at"] is not None}
        for r in rows
    ]


def unread_count(owner):
    return fetch_one("SELECT COUNT(*) AS n FROM notifications WHERE owner = ? AND read_at IS NULL", (owner,))["n"]


def notification_prefs(owner):
    user = get_user(owner, fresh=True)
    return {
        "inapp": bool(user["notify_inapp"]),
        "security": bool(user["notify_security"]),
        "messages": bool(user["notify_messages"]),
        "emails": emails_json(owner),
        "email_available": email_configured(),
        "verified_only": feature_on("email_verification"),
    }


@app.route("/api/notifications", methods=["GET"])
@require_auth
def api_notifications():
    """The in-app inbox (newest first) and where the user gets notifications."""
    return jsonify({
        "notifications": notifications_json(g.phone),
        "unread": unread_count(g.phone),
        "prefs": notification_prefs(g.phone),
        "keep_days": NOTIFICATION_DAYS,
    })


@app.route("/api/notifications/read", methods=["POST"])
@require_auth
def api_notifications_read():
    execute("UPDATE notifications SET read_at = ? WHERE owner = ? AND read_at IS NULL", (int(time.time()), g.phone))
    return jsonify({"unread": 0})


@app.route("/api/notifications/clear", methods=["POST"])
@require_auth
def api_notifications_clear():
    execute("DELETE FROM notifications WHERE owner = ?", (g.phone,))
    return jsonify({"cleared": True})


@app.route("/api/notifications/prefs", methods=["POST"])
@require_auth
def api_notification_prefs():
    """{inapp?, security?, messages?: bool} and/or {email_id, notify: bool} for one email.
    {all: false} stops every notification (all kinds, inbox and emails); {all: true} turns them back on."""
    body = body_json()
    flags = {"inapp": "notify_inapp", "security": "notify_security", "messages": "notify_messages"}
    if "all" in body:
        if not isinstance(body["all"], bool):
            return api_error("Expected all: true or false.", 400)
        on = int(body["all"])
        execute("UPDATE users SET notify_inapp = ?, notify_security = ?, notify_messages = ? WHERE phone = ?", (on, on, on, g.phone))
        execute("UPDATE user_emails SET notify = ? WHERE owner = ?", (on, g.phone))
    for key, column in flags.items():
        if key in body:
            if not isinstance(body[key], bool):
                return api_error(f"Expected {key}: true or false.", 400)
            execute(f"UPDATE users SET {column} = ? WHERE phone = ?", (int(body[key]), g.phone))
    if "email_id" in body:
        if not isinstance(body.get("notify"), bool):
            return api_error("Expected notify: true or false.", 400)
        if not fetch_one("SELECT 1 AS x FROM user_emails WHERE id = ? AND owner = ?", (body["email_id"], g.phone)):
            return api_error("Email not found.", 404)
        execute("UPDATE user_emails SET notify = ? WHERE id = ? AND owner = ?", (int(body["notify"]), body["email_id"], g.phone))
    return jsonify(notification_prefs(g.phone))


@app.route("/api/emails", methods=["GET"])
@require_auth
def api_emails():
    return jsonify({"emails": emails_json(g.phone), "max_emails": MAX_EMAILS})


@app.route("/api/emails", methods=["POST"])
@require_auth
def api_add_email():
    """Add an email (up to MAX_EMAILS). Any of them can be used to log in."""
    email = clean_email(body_json().get("email"))
    if not email:
        return api_error("Enter a valid email address.", 400)
    owner = email_owner(email)
    if owner == g.phone:
        return api_error("You've already added this email.", 409)
    if owner:
        return api_error(EMAIL_TAKEN, 409)
    if len(user_emails(g.phone)) >= MAX_EMAILS:
        return api_error(f"You can add up to {MAX_EMAILS} emails. Remove one first.", 409)
    execute("INSERT INTO user_emails (owner, email, created_at) VALUES (?, ?, ?)", (g.phone, email, int(time.time())))
    row = fetch_one("SELECT id, email, verified FROM user_emails WHERE email = ?", (email,))
    return jsonify({"id": row["id"], "email": row["email"], "verified": False}), 201


@app.route("/api/emails/<int:email_id>", methods=["DELETE"])
@require_auth
def api_delete_email(email_id):
    row = fetch_one("SELECT email FROM user_emails WHERE id = ? AND owner = ?", (email_id, g.phone))
    if not row:
        return api_error("Email not found.", 404)
    execute("DELETE FROM user_emails WHERE id = ? AND owner = ?", (email_id, g.phone))
    execute("DELETE FROM email_codes WHERE phone = ? AND email = ?", (g.phone, row["email"]))
    return jsonify({"deleted": email_id})


def email_to_verify(email_id):
    """(the user_emails row, None) or (None, (error, status))."""
    if not (feature_on("email_verification") and email_configured()):
        return None, ("Email verification is not available right now.", 503)
    row = fetch_one("SELECT id, email, verified FROM user_emails WHERE id = ? AND owner = ?", (email_id, g.phone))
    if not row:
        return None, ("Email not found.", 404)
    if row["verified"]:
        return None, ("This email is already verified.", 409)
    return row, None


@app.route("/api/email/verify/request", methods=["POST"])
@require_auth
def api_email_verify_request():
    """Email a 6-digit code to one of the account's addresses ({id})."""
    row, failed = email_to_verify(body_json().get("id"))
    if failed:
        return api_error(*failed)
    now = int(time.time())
    pending = fetch_one("SELECT sent_at FROM email_codes WHERE phone = ?", (g.phone,))
    if pending and now - pending["sent_at"] < RESET_RESEND_SECONDS:
        return api_error("A code was just sent. Wait a minute before asking for another.", 429)
    code = f"{secrets.randbelow(10 ** 6):06d}"
    db = get_db()
    db.execute(sql("DELETE FROM email_codes WHERE phone = ?"), (g.phone,))
    db.execute(
        sql("INSERT INTO email_codes (phone, email, code_hash, expires_at, sent_at) VALUES (?, ?, ?, ?, ?)"),
        (g.phone, row["email"], generate_password_hash(code), now + EMAIL_CODE_TTL, now),
    )
    db.commit()
    subject, text = render_message("email_code", {"code": code, "minutes": EMAIL_CODE_TTL // 60, "email": row["email"]})
    send_email_async(row["email"], subject, text)
    return jsonify({"sent": True, "email": row["email"]})


@app.route("/api/email/verify/confirm", methods=["POST"])
@require_auth
def api_email_verify_confirm():
    body = body_json()
    row, failed = email_to_verify(body.get("id"))
    if failed:
        return api_error(*failed)
    code = str(body.get("code", "")).strip()
    pending = fetch_one("SELECT email, code_hash, expires_at, attempts FROM email_codes WHERE phone = ?", (g.phone,))
    if not pending or pending["expires_at"] < time.time() or pending["email"] != row["email"]:
        return api_error("This code has expired. Ask for a new one.", 400)
    if not check_password_hash(pending["code_hash"], code):
        if pending["attempts"] + 1 >= MAX_RESET_ATTEMPTS:
            execute("DELETE FROM email_codes WHERE phone = ?", (g.phone,))
            return api_error("Too many wrong codes. Ask for a new one.", 429)
        execute("UPDATE email_codes SET attempts = attempts + 1 WHERE phone = ?", (g.phone,))
        return api_error("Wrong code.", 401)
    execute("DELETE FROM email_codes WHERE phone = ?", (g.phone,))
    execute("UPDATE user_emails SET verified = 1 WHERE id = ?", (row["id"],))
    return jsonify({"id": row["id"], "email": row["email"], "verified": True})


# ---------------------------------------------------------------- complaints
# Users report problems from the app (logged in) or the login screen (with a phone or email to
# reply to). The admin reads and answers them at /admin; logged-in users see the answer in the app.

COMPLAINT_MAX = 1000
COMPLAINTS_PER_HOUR = 5


def client_ip():
    return (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()[:64]


def optional_user():
    """The logged-in user's phone if a valid app token was sent, else None."""
    header = request.headers.get("Authorization", "")
    if not SECRET_KEY or not header.startswith("Bearer "):
        return None
    try:
        data = serializer().loads(header[7:], max_age=TOKEN_MAX_AGE)
    except BadSignature:
        return None
    user = get_user(data.get("phone", ""))
    return user["phone"] if user and token_version(user) == data.get("v") else None


def complaint_json(r):
    return {
        "id": r["id"], "message": r["message"], "status": r["status"], "reply": r["reply"],
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


@app.route("/api/complaints", methods=["POST"])
def api_add_complaint():
    body = body_json()
    message = str(body.get("message") or "").strip()[:COMPLAINT_MAX]
    owner = optional_user()
    contact = " ".join(str(body.get("contact") or "").split())[:120] or None
    if len(message) < 5:
        return api_error("Describe the problem in a few words.", 400)
    if not owner and not contact:
        return api_error("Enter a phone number or email so we can reply.", 400)
    now, ip = int(time.time()), client_ip()
    recent = fetch_one(
        "SELECT COUNT(*) AS n FROM complaints WHERE (ip = ? OR owner = ?) AND created_at > ?", (ip, owner or "", now - 3600)
    )
    if recent["n"] >= COMPLAINTS_PER_HOUR:
        return api_error("You've sent several reports already. Please wait a while.", 429)
    execute(
        "INSERT INTO complaints (owner, contact, message, ip, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (owner, contact, message, ip, now, now),
    )
    return jsonify({"sent": True}), 201


@app.route("/api/complaints", methods=["GET"])
@require_auth
def api_complaints():
    rows = fetch_all(
        "SELECT id, message, status, reply, created_at, updated_at FROM complaints WHERE owner = ? "
        "ORDER BY created_at DESC LIMIT 50",
        (g.phone,),
    )
    return jsonify({"complaints": [complaint_json(r) for r in rows]})


# ---------------------------------------------------------------- admin page (/admin)
# One password (ADMIN_PASSWORD) for the developer/admin. Feature switches, complaint handling,
# and a test email. Login is a signed, HttpOnly, SameSite=Strict cookie; forms carry a CSRF token.

ADMIN_MAX_TRIES = 5
_admin_failures = {}  # ip -> (count, locked_until); per process, enough to slow guessing


def admin_enabled():
    return bool(ADMIN_PASSWORD and SECRET_KEY)


def is_admin():
    return admin_enabled() and session.get("admin") is True


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def admin_post_ok():
    return is_admin() and hmac.compare_digest(str(request.form.get("csrf", "")), session.get("csrf", "") or "x")


def admin_stats():
    one = lambda q, p=(): fetch_one(q, p)["n"]
    return {
        "users": one("SELECT COUNT(*) AS n FROM users"),
        "contacts": one("SELECT COUNT(*) AS n FROM contacts"),
        "encrypted": one("SELECT COUNT(*) AS n FROM contacts WHERE enc = 1"),
        "emails": one("SELECT COUNT(*) AS n FROM user_emails"),
        "verified": one("SELECT COUNT(*) AS n FROM user_emails WHERE verified = 1"),
        "with_email": one("SELECT COUNT(DISTINCT owner) AS n FROM user_emails WHERE notify = 1"),
        "open": one("SELECT COUNT(*) AS n FROM complaints WHERE status = 'open'"),
    }


@app.after_request
def admin_no_cache(response):
    if request.path.startswith("/admin"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.route("/admin", methods=["GET"])
@app.route("/admin/", methods=["GET"])
def admin_page():
    if not admin_enabled():
        abort(404)
    if not is_admin():
        return render_template("admin.html", page="login", error=request.args.get("error"), csrf=csrf_token())
    show = request.args.get("show", "open")
    where = "" if show == "all" else "WHERE status = 'open'"
    complaints = fetch_all(
        f"SELECT id, owner, contact, message, status, reply, created_at, updated_at FROM complaints {where} "
        "ORDER BY created_at DESC LIMIT 200"
    )
    return render_template(
        "admin.html",
        page="dashboard",
        csrf=csrf_token(),
        stats=admin_stats(),
        features={name: feature_on(name, fresh=True) for name in FEATURES},
        key_set=bool(CONTACTS_CIPHER),
        email_ready=email_configured(),
        email_from=EMAIL_FROM,
        complaints=complaints,
        show=show,
        message=session.pop("flash", None),
        fmt_time=lambda t: time.strftime("%d %b %Y %H:%M", time.gmtime(t + 3 * 3600)) + " EAT",
        local=local_number,
    )


@app.route("/admin/login", methods=["POST"])
def admin_login():
    if not admin_enabled():
        abort(404)
    ip, now = client_ip(), time.time()
    count, locked_until = _admin_failures.get(ip, (0, 0))
    if locked_until > now:
        return redirect(url_for("admin_page", error="Too many wrong passwords. Try again in 15 minutes."))
    if not hmac.compare_digest(str(request.form.get("csrf", "")), session.get("csrf", "") or "x"):
        return redirect(url_for("admin_page", error="Your login form expired. Try again."))
    if not hmac.compare_digest(str(request.form.get("password", "")).encode(), ADMIN_PASSWORD.encode()):
        count += 1
        _admin_failures[ip] = (0, now + LOCKOUT_SECONDS) if count >= ADMIN_MAX_TRIES else (count, 0)
        log.warning("admin login failed from %s", ip)
        return redirect(url_for("admin_page", error="Wrong password."))
    _admin_failures.pop(ip, None)
    session.clear()
    session.permanent = True
    session["admin"] = True
    log.info("admin logged in from %s", ip)
    return redirect(url_for("admin_page"))


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    if admin_post_ok():
        session.clear()
    return redirect(url_for("admin_page"))


@app.route("/admin/feature", methods=["POST"])
def admin_feature():
    if not admin_post_ok():
        abort(403)
    name, on = request.form.get("name"), request.form.get("on") == "1"
    if name not in FEATURES:
        abort(400)
    if name == "encrypt_contacts":
        if on and not CONTACTS_CIPHER:
            session["flash"] = "Set CONTACTS_KEY on the server first (see README), then turn encryption on."
            return redirect(url_for("admin_page"))
        set_feature(name, on)
        changed = convert_contacts(encrypt=on) if CONTACTS_CIPHER else 0
        session["flash"] = f"Contact encryption {'on' if on else 'off'}: {changed} contacts {'encrypted' if on else 'decrypted'}."
    else:
        if on and not email_configured():
            session["flash"] = "Set SMTP_HOST and EMAIL_FROM (and SMTP_USER/SMTP_PASSWORD) on the server first."
            return redirect(url_for("admin_page"))
        set_feature(name, on)
        session["flash"] = f"{FEATURES[name]} turned {'on' if on else 'off'}."
    log.info("admin set %s = %s", name, on)
    return redirect(url_for("admin_page"))


@app.route("/admin/test-email", methods=["POST"])
def admin_test_email():
    if not admin_post_ok():
        abort(403)
    to = clean_email(request.form.get("to"))
    if not to:
        session["flash"] = "Enter a valid email address."
    elif not email_configured():
        session["flash"] = "Email is not configured (SMTP_HOST, EMAIL_FROM)."
    else:
        try:
            send_email(to, "Remote Phonebook test email", "Email from the Remote Phonebook server works.")
            session["flash"] = f"Test email sent to {to}."
        except Exception as e:
            log.exception("test email failed")
            session["flash"] = f"Test email failed: {type(e).__name__}: {e}"[:300]
    return redirect(url_for("admin_page"))


@app.route("/admin/notifications", methods=["GET"])
def admin_notifications():
    """Everything sent to users: security alerts, messages from the team and replies to reports."""
    if not admin_enabled():
        abort(404)
    if not is_admin():
        return redirect(url_for("admin_page"))
    # Separate filters, combined when several are filled in: topic, phone number, email, date range
    args = request.args
    kind = args.get("kind", "all")
    number = " ".join(str(args.get("number") or "").split())[:20]
    email = str(args.get("email") or "").strip().lower()[:120]
    date_from, date_to = str(args.get("from") or ""), str(args.get("to") or "")
    where, params, problems = [], [], []
    # Quick date ranges (Kenya time); each sets From/To and keeps the other filters
    today = datetime.datetime.fromtimestamp(time.time() + 3 * 3600, datetime.timezone.utc).date()
    first_of_month = today.replace(day=1)
    last_month_end = first_of_month - datetime.timedelta(days=1)
    one_day = datetime.timedelta(days=1)
    ranges = [
        ("Today", today, today),
        ("Yesterday", today - one_day, today - one_day),
        ("Last 7 days", today - 6 * one_day, today),
        ("Last 30 days", today - 29 * one_day, today),
        ("This month", first_of_month, today),
        ("Last month", last_month_end.replace(day=1), last_month_end),
    ]
    keep = {k: v for k, v in (("kind", kind if kind in LOG_KINDS else ""), ("number", number), ("email", email)) if v}
    quick = [
        {"label": label, "url": url_for("admin_notifications", **keep, **{"from": a.isoformat(), "to": b.isoformat()}),
         "active": (date_from, date_to) == (a.isoformat(), b.isoformat())}
        for label, a, b in ranges
    ]
    quick.append({"label": "All time", "url": url_for("admin_notifications", **keep), "active": not date_from and not date_to})
    if kind in LOG_KINDS:
        where.append("l.kind = ?")
        params.append(kind)
    if number:
        phone = login_phone(number)
        if phone:
            where.append("(l.owner = ? OR l.recipient = ? OR l.recipient = ?)")
            params += [phone, number, local_number(phone)]
        else:  # part of a number: match on the digits, in either 07... or +2547... form
            digits = re.sub(r"\D", "", number)
            tail = digits[1:] if digits.startswith("0") else digits
            where.append("(l.owner LIKE ? OR l.recipient LIKE ?)")
            params += [f"%{tail}%", f"%{digits}%"]
    if email:
        where.append("(LOWER(l.emails) LIKE ? OR LOWER(l.recipient) LIKE ?)")
        params += [f"%{email}%", f"%{email}%"]
    for value, op, extra in ((date_from, ">=", 0), (date_to, "<", 86400)):
        if value:
            try:  # dates are East Africa Time (UTC+3)
                day = calendar.timegm(time.strptime(value, "%Y-%m-%d")) - 3 * 3600  # midnight EAT
            except ValueError:
                problems.append(f"{value} isn't a date.")
                continue
            where.append(f"l.created_at {op} ?")
            params.append(day + extra)
    rows = fetch_all(
        "SELECT l.id, l.owner, l.recipient, l.kind, l.title, l.body, l.inapp_id, l.emails, l.email_status, l.note, "
        "l.complaint_id, l.created_at, n.read_at, n.id AS still_listed, c.message AS complaint_message "
        "FROM notification_log l LEFT JOIN notifications n ON n.id = l.inapp_id "
        "LEFT JOIN complaints c ON c.id = l.complaint_id "
        + ("WHERE " + " AND ".join(where) + " " if where else "")
        + "ORDER BY l.created_at DESC, l.id DESC LIMIT 300",
        tuple(params),
    )
    counts = {r["kind"]: r["n"] for r in fetch_all("SELECT kind, COUNT(*) AS n FROM notification_log GROUP BY kind")}
    return render_template(
        "admin.html",
        page="notifications",
        csrf=csrf_token(),
        rows=rows,
        kind=kind,
        number=number,
        email=email,
        date_from=date_from,
        date_to=date_to,
        filtered=bool(where),
        quick=quick,
        problems=problems,
        kinds=LOG_KINDS,
        counts=counts,
        keep_days=NOTIFICATION_LOG_DAYS,
        stats=admin_stats(),
        fmt_time=lambda t: time.strftime("%d %b %Y %H:%M", time.gmtime(t + 3 * 3600)) + " EAT",
        local=local_number,
    )


@app.route("/admin/templates", methods=["GET"])
def admin_templates():
    """Edit every notification and email: on/off, subject, text; preview and test."""
    if not admin_enabled():
        abort(404)
    if not is_admin():
        return redirect(url_for("admin_page"))
    items = []
    for key, t in TEMPLATES.items():
        sample = {**SAMPLE_VARS}
        subject, body = render_message(key, sample)
        items.append({
            "key": key, "label": t["label"], "kind": t["kind"], "enabled": template_on(key),
            "subject": template_part(key, "subject"), "body": template_part(key, "body"),
            "custom": bool(setting_text(f"tpl:{key}:subject") or setting_text(f"tpl:{key}:body")),
            "vars": {**({} if key == "email_code" else {"name": COMMON_VARS["name"], "phone": COMMON_VARS["phone"]}),
                     "app": COMMON_VARS["app"], "ussd_code": COMMON_VARS["ussd_code"], **t["vars"]},
            "preview_subject": subject, "preview_body": body,
        })
    parts = [{"key": k, "label": label, "value": email_part(k), "custom": bool(setting_text(f"email:{k}"))}
             for k, (label, _default) in EMAIL_PARTS.items()]
    return render_template(
        "admin.html", page="templates", csrf=csrf_token(), items=items, parts=parts, stats=admin_stats(),
        email_ready=email_configured(), email_from=EMAIL_FROM, message=session.pop("flash", None),
    )


@app.route("/admin/templates/<key>", methods=["POST"])
def admin_save_template(key):
    if not admin_post_ok():
        abort(403)
    action = request.form.get("action", "save")
    if key == "email_parts":
        for k in EMAIL_PARTS:
            value = "" if action == "reset" else str(request.form.get(k) or "").strip()[:300]
            if action != "reset" and not value:
                value = EMAIL_PARTS[k][1] if k == "sender_name" else NONE  # a sender name is required
            set_setting_text(f"email:{k}", "" if value == EMAIL_PARTS[k][1] else value)
        session["flash"] = "Email sender, footer and signature " + ("reset." if action == "reset" else "saved.")
        return redirect(url_for("admin_templates") + "#email_parts")
    if key not in TEMPLATES:
        abort(404)
    if action == "reset":
        for part in ("subject", "body", "enabled"):
            set_setting_text(f"tpl:{key}:{part}", "")
        session["flash"] = f"“{TEMPLATES[key]['label']}” reset to the default."
        return redirect(url_for("admin_templates") + f"#{key}")
    subject = " ".join(str(request.form.get("subject") or "").split())[:200]
    body = str(request.form.get("body") or "").replace("\r\n", "\n").strip()[:2000]
    if not subject or not body:
        session["flash"] = "A subject and text are needed (use Reset to go back to the default)."
        return redirect(url_for("admin_templates") + f"#{key}")
    set_setting_text(f"tpl:{key}:subject", "" if subject == TEMPLATES[key]["subject"] else subject)
    set_setting_text(f"tpl:{key}:body", "" if body == TEMPLATES[key]["body"] else body)
    set_setting_text(f"tpl:{key}:enabled", "" if request.form.get("enabled") else "0")
    if action == "test":
        to = clean_email(request.form.get("test_to"))
        if not to or not email_configured():
            session["flash"] = "Saved. To send a test, enter an email address (and configure email)."
        else:
            test_subject, test_body = render_message(key, SAMPLE_VARS)
            footer = email_part("notify_footer") if TEMPLATES[key]["kind"] != "email" else ""
            try:
                send_email(to, "[Test] " + test_subject, test_body + (f"\n\n{footer}" if footer.strip() else ""))
                session["flash"] = f"Saved, and a test of “{TEMPLATES[key]['label']}” was sent to {to}."
            except Exception as e:
                session["flash"] = f"Saved, but the test email failed: {type(e).__name__}: {e}"[:300]
    else:
        session["flash"] = f"“{TEMPLATES[key]['label']}” saved."
    return redirect(url_for("admin_templates") + f"#{key}")


@app.route("/admin/message", methods=["POST"])
def admin_message():
    """A message from the team: to every account, or one account (phone number). It goes to each
    user's in-app inbox and their chosen emails (unless they turned off messages from the team)."""
    if not admin_post_ok():
        abort(403)
    subject = " ".join(str(request.form.get("subject") or "").split())[:150]
    body = str(request.form.get("body") or "").strip()[:2000]
    to = str(request.form.get("to") or "all").strip()
    if not subject or not body:
        session["flash"] = "Enter a subject and a message."
        return redirect(url_for("admin_page") + "#message")
    if to == "all":
        owners = [r["phone"] for r in fetch_all("SELECT phone FROM users")]
    else:
        phone = login_phone(to)
        if not phone or not get_user(phone):
            session["flash"] = f"No account for {to}."
            return redirect(url_for("admin_page") + "#message")
        owners = [phone]
    emailed = reached = 0
    for owner in owners:
        sent = notify(owner, subject, body, kind="messages")
        emailed += len(sent)
        reached += 1 if get_user(owner)["notify_messages"] else 0
    session["flash"] = (
        f"Message sent to {reached} of {len(owners)} account{'s' if len(owners) != 1 else ''} "
        f"({emailed} email{'s' if emailed != 1 else ''}"
        + ("" if email_configured() else "; email isn't configured, so in the app only") + ")."
    )
    log.info("admin message to %s: %s", to, subject)
    return redirect(url_for("admin_page") + "#message")


@app.route("/admin/complaints/<int:complaint_id>", methods=["POST"])
def admin_complaint(complaint_id):
    if not admin_post_ok():
        abort(403)
    row = fetch_one("SELECT owner, contact, message, status, reply FROM complaints WHERE id = ?", (complaint_id,))
    if not row:
        abort(404)
    reply = str(request.form.get("reply") or "").strip()[:COMPLAINT_MAX] or row["reply"]
    status = request.form.get("status") if request.form.get("status") in ("open", "resolved") else row["status"]
    execute(
        "UPDATE complaints SET reply = ?, status = ?, updated_at = ? WHERE id = ?",
        (reply, status, int(time.time()), complaint_id),
    )
    sent_to = []
    if reply != row["reply"] or status != row["status"]:
        values = {
            "report_id": complaint_id, "status": "resolved" if status == "resolved" else "answered", "report": row["message"],
            "reply": reply or ("We've marked your report as resolved." if status == "resolved" else "We've reopened your report."),
        }
        subject, text = render_message("report_reply", {"name": "there", "phone": row["contact"] or "", **values})
        if row["owner"]:
            sent_to = notify_event(row["owner"], "report_reply", complaint_id=complaint_id, **values)
        elif reply and not template_on("report_reply"):
            log_notification(None, row["contact"], "reply", subject, text, complaint_id=complaint_id,
                             note="Not sent: this notification is turned off in admin")
        elif reply and email_configured() and clean_email(row["contact"]):
            sent_to = [clean_email(row["contact"])]
            log_id = log_notification(None, row["contact"], "reply", subject, text, emails=sent_to, complaint_id=complaint_id)
            send_emails_async([(sent_to[0], subject, text)], log_id)
        elif reply:
            log_notification(None, row["contact"], "reply", subject, text, complaint_id=complaint_id,
                             note="Not sent: they left a phone number, not an email, and weren't logged in"
                             if row["contact"] else "Not sent: no way to reach them")
    session["flash"] = f"Report #{complaint_id} saved." + (
        f" Reply emailed to {', '.join(sent_to)}." if sent_to else " (Not emailed: no email to send it to.)" if reply else ""
    )
    return redirect(url_for("admin_page", show=request.form.get("show", "open")))


# ---------------------------------------------------------------- website
# The Remote Phonebook website: the mobile app built for the web (npm run build:web in mobile/),
# served from the same address as the API.

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


@app.route("/", methods=["GET"])
def website():
    if not os.path.exists(os.path.join(WEB_DIR, "index.html")):
        return health()
    response = send_from_directory(WEB_DIR, "index.html")
    response.headers["Cache-Control"] = "no-cache"  # always pick up a new build
    return response


@app.route("/_expo/<path:filename>", methods=["GET"])
def website_assets(filename):
    response = send_from_directory(os.path.join(WEB_DIR, "_expo"), filename)
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"  # file names change with each build
    return response


@app.route("/assets/<path:filename>", methods=["GET"])
def website_images(filename):
    """Images bundled into the website (e.g. the logo in the header); names change with their content."""
    response = send_from_directory(os.path.join(WEB_DIR, "assets"), filename)
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


@app.route("/<any('favicon.ico', 'favicon.png', 'apple-touch-icon.png'):filename>", methods=["GET"])
def website_icons(filename):
    """The Oduma Corp icon: browser tab and iPhone home screen."""
    response = send_from_directory(WEB_DIR, filename)
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)), debug=True)
