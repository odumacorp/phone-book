"""Recording what the organisation dashboard shows. Every function here swallows its own errors: recording must never
break USSD, the app or the platform."""
import hashlib
import hmac
import time

from flask import g, has_request_context, request

from pb.config import SECRET_KEY, log
from pb.db import execute, get_db, sql

USSD_TIMEOUT_SECONDS = 8  # Africa's Talking waits about 10 s for a USSD screen; slower than this counts as a timeout


def _safe(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            log.exception("dashboard tracking failed (%s)", fn.__name__)
            return None
    wrapper.__name__ = fn.__name__
    return wrapper


def api_channel():
    """The app sends X-Platform (android/ios/web); older apps don't: the website names itself "... (website)"."""
    if not has_request_context():
        return "app"
    platform = (request.headers.get("X-Platform") or "").lower()
    if platform in ("android", "ios", "web"):
        return platform
    return "web" if "(website)" in request.headers.get("X-Device-Name", "") else "android"


# ---------------------------------------------------------------- USSD sessions

@_safe
def mark_reached_contact():
    """The member reached a contact in this USSD session (so it isn't a drop-off)."""
    login_id = getattr(g, "ussd_login_id", None) if has_request_context() else None
    if login_id:
        execute("UPDATE ussd_sessions SET reached_contact = 1 WHERE id = ? AND reached_contact = 0", (login_id,))


@_safe
def ussd_failure(caller, kind):
    execute("INSERT INTO ussd_failures (caller, kind, created_at) VALUES (?, ?, ?)", (caller, kind, int(time.time())))


# ---------------------------------------------------------------- lookups

def _preview():
    return has_request_context() and getattr(g, "ussd_simulate", False)  # an admin's Preview: not a real member


@_safe
def lookup(owner, channel, kind="contacts", tenant_id=None):
    if owner and not _preview():
        execute("INSERT INTO lookups (owner, tenant_id, channel, kind, created_at) VALUES (?, ?, ?, ?, ?)",
                (owner, tenant_id, channel, kind, int(time.time())))
        if channel == "ussd":
            mark_reached_contact()


# ---------------------------------------------------------------- deliveries (notices and reminders)

@_safe
def sent(tenant_id, kind, item_id, phones, channel, delivered=False, provider_ids=None):
    """Record that a notice or reminder went to these people. delivered: it's in their app or on their notice board
    already (SMS waits for the delivery report)."""
    now = int(time.time())
    db = get_db()
    provider_ids = provider_ids or {}
    for phone in dict.fromkeys(phones):
        db.execute(sql("INSERT INTO deliveries (tenant_id, kind, item_id, owner, channel, provider_id, status, sent_at, delivered_at) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"),
                   (tenant_id, kind, item_id, phone, channel, provider_ids.get(phone), "delivered" if delivered else "sent",
                    now, now if delivered else None))
    db.commit()


@_safe
def viewed(tenant_id, kind, item_ids, owner):
    """The member opened these notices (USSD or app): the first view counts."""
    if not item_ids or _preview():
        return
    marks = ", ".join("?" for _ in item_ids)
    execute(f"UPDATE deliveries SET viewed_at = ? WHERE tenant_id = ? AND kind = ? AND owner = ? AND viewed_at IS NULL "
            f"AND item_id IN ({marks})", (int(time.time()), tenant_id, kind, owner, *item_ids))


@_safe
def sms_ids(response):
    """Africa's Talking's send response -> {phone: messageId} (to match delivery reports later)."""
    recipients = ((response or {}).get("SMSMessageData") or {}).get("Recipients") or []
    return {r.get("number"): r.get("messageId") for r in recipients if r.get("number") and r.get("messageId")}


def delivery_report_token():
    """Part of the delivery-report URL registered with Africa's Talking, so nobody else can post reports."""
    return hmac.new((SECRET_KEY or "dev").encode(), b"sms-delivery-reports", hashlib.sha256).hexdigest()[:24]


@_safe
def delivery_report(message_id, status):
    """Africa's Talking delivery report: Success -> delivered; Failed/Rejected -> failed."""
    if not message_id:
        return
    state = {"success": "delivered", "failed": "failed", "rejected": "failed"}.get(str(status or "").lower())
    if not state:
        return  # Sent / Buffered / Submitted: still on its way
    now = int(time.time())
    execute("UPDATE deliveries SET status = ?, delivered_at = ? WHERE provider_id = ? AND status = 'sent'",
            (state, now if state == "delivered" else None, message_id))
