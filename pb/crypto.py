import base64
import os
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from pb.config import CONTACTS_KEY
from pb.db import fetch_all, get_db, sql
from pb.features import feature_on


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
