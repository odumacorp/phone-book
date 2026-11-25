"""Profile photos: set, remove and read a person's picture (stored in user_photos, base64)."""
import base64
import binascii
import re
import time

from pb.db import execute, fetch_one

MAX_BYTES = 400 * 1024  # the app sends ~320px JPEGs (20-60 KB); this leaves room for PNGs from the website
DATA_URL_RE = re.compile(r"^data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=\s]+)$")
SIGNATURES = {"image/jpeg": (b"\xff\xd8\xff",), "image/png": (b"\x89PNG\r\n\x1a\n",), "image/webp": (b"RIFF",)}


def clean(data_url, max_bytes=MAX_BYTES):
    """A data URL (from the app, or a file upload turned into one) -> (mime, base64) or ValueError.
    Only JPEG, PNG and WebP whose bytes really are that kind of picture (never SVG: it can carry scripts)."""
    match = DATA_URL_RE.match(str(data_url or "").strip())
    if not match:
        raise ValueError("Choose a JPEG, PNG or WebP picture.")
    mime, raw = match.group(1), re.sub(r"\s", "", match.group(2))
    try:
        image = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("That picture couldn't be read. Try another one.") from None
    if not image or len(image) > max_bytes:
        raise ValueError("That picture is too big. Choose a smaller one.")
    if not image.startswith(SIGNATURES[mime]) or (mime == "image/webp" and image[8:12] != b"WEBP"):
        raise ValueError("That file isn't a picture. Choose a JPEG, PNG or WebP photo.")
    return mime, base64.b64encode(image).decode()


def set_photo(phone, data_url):
    mime, data = clean(data_url)
    execute(
        "INSERT INTO user_photos (phone, mime, data, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (phone) DO UPDATE SET mime = excluded.mime, data = excluded.data, updated_at = excluded.updated_at",
        (phone, mime, data, int(time.time())),
    )
    return photo_url(phone)


def remove_photo(phone):
    execute("DELETE FROM user_photos WHERE phone = ?", (phone,))


def photo_url(phone):
    """The picture as a data URL (shown straight in the app), or None: the app then shows a person icon."""
    row = fetch_one("SELECT mime, data FROM user_photos WHERE phone = ?", (phone,))
    return f"data:{row['mime']};base64,{row['data']}" if row else None


def data_url_from_upload(file_storage):
    """A file from a form (<input type=file>) -> data URL for clean(), or None when no file was chosen."""
    if not file_storage or not file_storage.filename:
        return None
    raw = file_storage.read(MAX_BYTES * 2 + 1)
    mime = (file_storage.mimetype or "").lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"
