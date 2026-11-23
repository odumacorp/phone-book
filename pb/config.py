"""Settings from the environment (.env locally, Render env vars in production) and shared constants."""
import logging
import os
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the repository: .env, web/, templates/
load_dotenv(os.path.join(ROOT, ".env"))

AT_USERNAME = os.getenv("AT_USERNAME", "sandbox")
AT_API_KEY = os.getenv("AT_API_KEY", "")
AT_SENDER_ID = os.getenv("AT_SENDER_ID") or None
AT_VOICE_NUMBER = os.getenv("AT_VOICE_NUMBER") or None  # AT virtual number used for "Call"
DATABASE_URL = os.getenv("DATABASE_URL")  # Postgres (Neon pooled); falls back to SQLite when unset
DATABASE_URL_UNPOOLED = os.getenv("DATABASE_URL_UNPOOLED") or DATABASE_URL  # direct, for schema setup
DB_PATH = os.getenv("DB_PATH", os.path.join(ROOT, "phonebook.db"))
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
SMS_CHUNK = 459  # contact lists: up to 3 joined SMS parts per message
SMS_MAX_MESSAGES = 5  # "SMS me my contacts" sends at most this many messages; the rest are in the app
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

BACK, HOME, LOGOUT = "0", "00", "000"
NAV = "0.Back 00.Home 000.Logout"  # on every screen after login
