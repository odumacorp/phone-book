"""PostHog product analytics, anonymously.

Off until POSTHOG_KEY (the project API key, "phc_...") is set; POSTHOG_HOST picks the region
(default https://us.i.posthog.com; EU: https://eu.i.posthog.com).

Privacy (Kenya Data Protection Act): people are sent only as an anonymous ID made from their account with a server-side
secret ("u_..."; staff "s_..."), never their phone number, name or email. Event properties are counts, channels and
organisation ids, never contact details. Events go out on a background thread and never slow or break a request.
"""
import hashlib
import hmac
import json
import os
import re
import threading
import time
import urllib.request

from pb.config import SECRET_KEY, log

POSTHOG_KEY = os.getenv("POSTHOG_KEY", "").strip()
POSTHOG_HOST = (os.getenv("POSTHOG_HOST", "").strip() or "https://us.i.posthog.com").rstrip("/")
_SALT = (os.getenv("ANALYTICS_SALT") or SECRET_KEY or "remote-phonebook").encode()
PHONE_IN_TEXT = re.compile(r"\+?\d{9,15}")
SAFE_KEY = re.compile(r"^[a-z0-9_$]{1,40}$")


def enabled():
    return bool(POSTHOG_KEY)


def _hash(text):
    return hmac.new(_SALT, str(text).encode(), hashlib.sha256).hexdigest()[:24]


def person_id(phone):
    """The anonymous ID for an account (the same in the app, the website, the platform and on USSD)."""
    return f"u_{_hash(phone)}" if phone else None


def actor_id(actor):
    """An audit actor ("user:+254...", "staff:3", "staff:root", "system", "cli") -> anonymous ID."""
    actor = str(actor or "system")
    if actor.startswith("user:"):
        return person_id(actor[5:])
    if actor.startswith("staff:"):
        return f"s_{_hash(actor)}"
    return f"sys_{actor}"[:40]


def clean_path(path):
    """A page address without phone numbers (e.g. /platform/people/254712345678 -> /platform/people/:phone)."""
    return PHONE_IN_TEXT.sub(":phone", str(path or ""))[:300]


def _clean_props(props):
    out = {}
    for key, value in (props or {}).items():
        if not SAFE_KEY.match(str(key)) or value is None:
            continue
        if isinstance(value, (bool, int, float)):
            out[key] = value
        elif isinstance(value, str):
            out[key] = PHONE_IN_TEXT.sub(":phone", value)[:200]
    return out


def _send(payload):
    try:
        req = urllib.request.Request(f"{POSTHOG_HOST}/i/v0/e/", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as e:  # analytics must never break anything
        log.info("PostHog event not sent: %s", e)


def track(event, distinct_id, tenant_id=None, **props):
    """Send one event (no-op without POSTHOG_KEY). props: counts, channels, kinds; never personal details."""
    if not enabled() or not distinct_id or not event:
        return
    properties = _clean_props(props)
    properties["$geoip_disable"] = True  # don't locate people from the server's address
    properties["source"] = "server"
    if tenant_id:
        properties["$groups"] = {"organisation": str(tenant_id)}
        properties["organisation_id"] = int(tenant_id) if str(tenant_id).isdigit() else str(tenant_id)
    payload = {"api_key": POSTHOG_KEY, "event": str(event)[:100], "distinct_id": distinct_id, "properties": properties,
               "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _dispatch(payload)


def _dispatch(payload):
    threading.Thread(target=_send, args=(payload,), daemon=True).start()


def track_user(event, phone, tenant_id=None, **props):
    track(event, person_id(phone), tenant_id, **props)


def public_config():
    """For the app and website: whether analytics is on, and where to send events (the key is public by design)."""
    return {"key": POSTHOG_KEY, "host": POSTHOG_HOST} if enabled() else None
