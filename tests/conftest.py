"""Test setup: a throwaway SQLite database, no real SMS/email/voice, and helpers to drive USSD sessions.

The environment is set before the app is imported, so .env (Neon, Africa's Talking keys) is never used:
python-dotenv doesn't override variables that are already set, even to "".
"""
import base64
import itertools
import os
import sqlite3
import sys
import tempfile
import threading
import time

import pytest

_tmp = tempfile.mkdtemp(prefix="pb-tests-")
# TEST_DATABASE_URL runs the suite on Postgres (a throwaway database: every table is emptied per test)
PG_URL = os.environ.get("TEST_DATABASE_URL", "")
os.environ.update({
    "DATABASE_URL": PG_URL,
    "DATABASE_URL_UNPOOLED": PG_URL,
    "DB_PATH": os.path.join(_tmp, "test.db"),
    "AT_API_KEY": "",
    "AT_VOICE_NUMBER": "",
    "SECRET_KEY": "test-secret-key",
    "ADMIN_PASSWORD": "admin-pass",
    "TICK_SECRET": "tick-secret",
    "CONTACTS_KEY": base64.urlsafe_b64encode(b"k" * 32).decode(),
    "SMTP_HOST": "",
    "SMTP_USER": "",
    "EMAIL_FROM": "",
    "RENDER": "",
})
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module  # noqa: E402

USSD_MAX_LEN = 182


class FakeSMS:
    """Stands in for africastalking.SMS; records what would have been sent."""

    def __init__(self):
        self.sent = []
        self._lock = threading.Lock()

    def send(self, message, recipients, sender_id=None):
        with self._lock:
            for to in recipients:
                self.sent.append((to, message))
        return {"SMSMessageData": {"Recipients": [{"status": "Success"}]}}

    def wait(self, count=1, timeout=2.0):
        """SMS are sent from background threads: wait until at least `count` have arrived."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if len(self.sent) >= count:
                    return list(self.sent)
            time.sleep(0.01)
        return list(self.sent)

    def to(self, phone, count=1):
        return [m for to, m in self.wait(count) if to == phone]


def _wipe_database():
    if PG_URL:
        import psycopg
        with psycopg.connect(PG_URL, autocommit=True) as db:
            tables = [r[0] for r in db.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")]
            tables = [t for t in tables if t != "schema_migrations"]
            if tables:
                db.execute("TRUNCATE " + ", ".join(tables) + " RESTART IDENTITY")
        return
    db = sqlite3.connect(os.environ["DB_PATH"])
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        if table != "schema_migrations":
            db.execute(f"DELETE FROM {table}")
    db.commit()
    db.close()


@pytest.fixture(autouse=True)
def clean_state():
    _wipe_database()
    app_module.app.config["TESTING"] = True
    for cache in _caches():
        cache.clear()
    yield


def _pb_modules():
    return [m for name, m in list(sys.modules.items()) if name == "pb" or name.startswith("pb.")]


def _caches():
    """Per-process caches that must not leak between tests."""
    return [getattr(m, n) for m in _pb_modules() for n in ("_settings_cache", "_admin_failures") if hasattr(m, n)]


@pytest.fixture
def client():
    return app_module.app.test_client()


@pytest.fixture
def fake_sms(monkeypatch):
    fake = FakeSMS()
    for target in sms_targets():
        monkeypatch.setattr(target, "sms", fake)
    return fake


def sms_targets():
    """Modules holding the Africa's Talking SMS client (each `from pb.messaging import sms` is its own reference)."""
    return [m for m in _pb_modules() if hasattr(m, "sms")]


_session_ids = itertools.count(1)


class UssdSession:
    def __init__(self, client, caller, network="63902"):
        self.client, self.caller, self.network = client, caller, network
        self.session_id = f"ATUid_test_{next(_session_ids)}"
        self.inputs = []
        self.last = None

    def send(self, value=None):
        """Type one input (None = the initial dial). Returns the response text."""
        if value is not None:
            self.inputs.append(value)
        resp = self.client.post("/ussd", data={
            "sessionId": self.session_id,
            "serviceCode": "*384*88041#",
            "phoneNumber": self.caller,
            "networkCode": self.network,
            "text": "*".join(self.inputs),
        })
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert body.startswith(("CON ", "END ")), body
        assert len(body) - 4 <= USSD_MAX_LEN, f"screen too long ({len(body) - 4}): {body!r}"
        self.last = body
        return body


@pytest.fixture
def dial(client):
    def _dial(caller, network="63902"):
        session = UssdSession(client, caller, network)
        session.send()
        return session
    return _dial


@pytest.fixture
def register(client, fake_sms):
    """Register an account through the real app API (SMS code included). Returns its app token."""
    def _register(phone="0712345678", pin="1234", password="secret-pass-1", name="Asha", email=""):
        resp = client.post("/api/register/request", json={"phone": phone, "email": email})
        assert resp.status_code == 200, resp.get_json()
        full = resp.get_json()["phone"]
        code = next(m for to, m in reversed(fake_sms.wait()) if to == full).split(" code is ")[1][:6]
        resp = client.post("/api/register/confirm", json={
            "phone": phone, "code": code, "name": name, "password": password, "pin": pin, "email": email,
        })
        assert resp.status_code == 200, resp.get_json()
        return resp.get_json()["token"]
    return _register


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def login_ussd(dial, caller, phone="0712345678", pin="1234"):
    """A fresh phone-number + PIN login (declines the "Welcome back" offer of a recent session)."""
    s = dial(caller)
    if s.last.startswith("CON Welcome back"):
        s.send("0")
    s.send(phone)
    s.send(pin)
    return s
