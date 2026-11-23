import time

from pb.db import execute, fetch_one


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
