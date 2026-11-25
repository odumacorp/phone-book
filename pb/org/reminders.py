"""Reminders: a short message an operator sends to members by SMS, in the app (Notifications), or both,
now or at a chosen time. Who gets it: everyone, chosen roles, and/or one department or group.

- App: a notification in each registered member's in-app Notifications list (if they haven't turned that list off).
- SMS: to every recipient's phone, registered or not, through Africa's Talking, in batches from a background thread.
- Scheduled reminders are sent by the "send_due_reminders" job (POST /internal/tick, every minute).
Sending is claimed with one atomic UPDATE, so a reminder is never sent twice. Needs announcements.publish.
The due-reminder scan in due_reminders() spans organisations on purpose (it's the platform's job).
"""
import datetime
import math
import threading
import time

from pb import messaging
from pb.audit import audit
from pb.config import AT_SENDER_ID, SMS_CHUNK, log
from pb.db import fetch_all, get_db, sql
from pb.org import content
from pb.tenancy import tenants as tenancy
from pb.tenancy.repo import TenantRepo
from pb.users import get_user

TITLE_MAX, BODY_MAX = 60, 300
CHANNELS = {"app": "In the app", "sms": "By SMS", "both": "SMS and app"}
SMS_BATCH = 100
MAX_AHEAD_DAYS = 365


class NotAllowed(Exception):
    pass


def sms_text(org_name, title, body):
    return f"{org_name}: {title}\n{body}"[:SMS_CHUNK]


def sms_parts(text):
    """How many SMS a message takes (160 characters for one, 153 per part when joined)."""
    return 1 if len(text) <= 160 else math.ceil(len(text) / 153)


# ---------------------------------------------------------------- who gets it

def recipients(tenant_id, audience="", placement=""):
    """Active members' phones matching the audience (roles, departments, groups; "" = everyone) and, optionally,
    one department or group (placement "d:<id>" / "g:<id>", including the ones inside it)."""
    phones = []
    for phone, tags in content.member_tags(tenant_id).items():
        if content.matches(audience, tags) and (not placement or placement in tags):
            phones.append(phone)
    return phones


def estimate(tenant_id, audience, placement, channels, title="", body=""):
    """Numbers for the form before sending: how many people, how many have the app, how many SMS."""
    phones = recipients(tenant_id, audience, placement)
    with_app = sum(1 for p in phones if (u := get_user(p)) and u["password_hash"] and u["notify_inapp"])
    org = tenancy.tenant(tenant_id)
    parts = sms_parts(sms_text(org["name"], title or "x", body or "x"))
    return {
        "people": len(phones),
        "app": with_app if channels in ("app", "both") else 0,
        "sms": len(phones) if channels in ("sms", "both") else 0,
        "sms_parts": parts,
        "sms_total": (len(phones) * parts) if channels in ("sms", "both") else 0,
        "sms_ready": messaging.sms is not None,
    }


# ---------------------------------------------------------------- create, cancel, list

EAT = content.EAT


def parse_send_at(form, now=None):
    now = now or int(time.time())
    if form.get("when", "now") == "now":
        return now
    raw = str(form.get("send_at") or "").strip()
    try:
        moment = datetime.datetime.fromisoformat(raw).replace(tzinfo=EAT)
    except ValueError:
        raise ValueError("Pick the date and time to send it.") from None
    at = int(moment.timestamp())
    if at < now - 60:
        raise ValueError("That time has passed. Pick a time in the future, or choose Send now.")
    if at - now > MAX_AHEAD_DAYS * 86400:
        raise ValueError("Reminders can be scheduled up to a year ahead.")
    return at


def create(principal, tenant_id, form):
    if not content.can_manage(principal, tenant_id, "announcements.publish"):
        raise NotAllowed("You don't have permission to send reminders.")
    title = " ".join(str(form.get("title") or "").split())[:TITLE_MAX]
    body = str(form.get("body") or "").strip()[:BODY_MAX]
    channels = form.get("channels") or "both"
    if not title:
        raise ValueError("Give the reminder a short title, e.g. \"Fees due Friday\".")
    if not body:
        raise ValueError("Write the reminder.")
    if channels not in CHANNELS:
        raise ValueError("Choose how to send it.")
    if channels == "sms" and messaging.sms is None:
        raise ValueError("SMS isn't set up on the server yet. Choose In the app, or ask Oduma Corp to set up SMS.")
    audience = content.parse_audience(form.get("audience"), tenant_id)
    placement = str(form.get("placement") or "")
    if placement and placement[:2] not in ("d:", "g:"):
        raise ValueError("Choose a department or group.")
    if not recipients(tenant_id, audience, placement):
        raise ValueError("Nobody matches who you chose. Pick everyone, or other roles or groups.")
    now = int(time.time())
    send_at = parse_send_at(form, now)
    notice_id = int(form["notice_id"]) if str(form.get("notice_id") or "").isdigit() else None
    reminder_id = TenantRepo(tenant_id).insert(
        "reminders", title=title, body=body, channels=channels, audience=audience, placement=placement, notice_id=notice_id,
        send_at=send_at, status="scheduled", created_by=principal, created_at=now,
    )
    audit("reminder.create", tenant_id, ("reminder", reminder_id),
          {"title": title, "channels": channels, "send_at": send_at, "scheduled": send_at > now + 60}, actor=principal)
    if send_at <= now + 60:
        send(tenant_id, reminder_id)
    return reminder_id


def cancel(principal, tenant_id, reminder_id):
    if not content.can_manage(principal, tenant_id, "announcements.publish"):
        raise NotAllowed("You don't have permission to cancel reminders.")
    if not TenantRepo(tenant_id).update("reminders", "id = ? AND status = 'scheduled'", (reminder_id,), status="cancelled"):
        raise ValueError("Only reminders that haven't been sent yet can be cancelled.")
    audit("reminder.cancel", tenant_id, ("reminder", reminder_id), None, actor=principal)


def reminders(tenant_id, limit=100):
    return TenantRepo(tenant_id).all("reminders", "deleted_at IS NULL", order="send_at DESC", limit=limit)


def reminder(tenant_id, reminder_id):
    return TenantRepo(tenant_id).one("reminders", "id = ?", (reminder_id,))


# ---------------------------------------------------------------- sending

def send(tenant_id, reminder_id):
    """Send one reminder now, if nobody else has. Returns the counts, or None if it was already taken."""
    repo = TenantRepo(tenant_id)
    if not repo.update("reminders", "id = ? AND status = 'scheduled'", (reminder_id,), status="sending"):
        return None
    row = repo.one("reminders", "id = ?", (reminder_id,))
    org = tenancy.tenant(tenant_id)
    notes = []
    if not org or org["status"] in ("suspended", "closed"):
        repo.update("reminders", "id = ?", (reminder_id,), status="failed", note="The organisation is suspended.", sent_at=int(time.time()))
        return None
    phones = recipients(tenant_id, row["audience"], row["placement"])
    now = int(time.time())
    app_sent = sms_sent = parts = 0
    if row["channels"] in ("app", "both"):
        app_sent = app_notify(phones, f"{org['name']}: {row['title']}", row["body"], now)
        if app_sent < len(phones):
            notes.append(f"{len(phones) - app_sent} without the app (or with notifications off)")
    if row["channels"] in ("sms", "both"):
        if messaging.sms is None:
            notes.append("SMS isn't set up on the server, so no SMS was sent")
        elif phones:
            text = sms_text(org["name"], row["title"], row["body"])
            parts = sms_parts(text) * len(phones)
            sms_sent = len(phones)
            send_bulk_sms(phones, text)
    repo.update("reminders", "id = ?", (reminder_id,), status="sent", sent_at=now, recipients=len(phones),
                app_sent=app_sent, sms_sent=sms_sent, sms_parts=parts, note="; ".join(notes) or None)
    audit("reminder.sent", tenant_id, ("reminder", reminder_id),
          {"recipients": len(phones), "app": app_sent, "sms": sms_sent, "sms_parts": parts}, actor="system")
    return {"recipients": len(phones), "app": app_sent, "sms": sms_sent}


def app_notify(phones, title, body, now=None):
    """Put a notification in each registered person's in-app Notifications (unless they turned the list off).
    Returns how many got it."""
    now = now or int(time.time())
    rows = []
    for phone in dict.fromkeys(phones):
        user = get_user(phone)
        if user and user["notify_inapp"]:
            rows.append((phone, "org", title[:200], body[:2000], now))
    if rows:
        db = get_db()
        db.cursor().executemany(sql("INSERT INTO notifications (owner, kind, title, body, created_at) VALUES (?, ?, ?, ?, ?)"), rows)
        db.commit()
    return len(rows)


def send_bulk_sms(phones, text):
    """Africa's Talking takes many recipients per request: send in batches from a background thread."""
    client = messaging.sms

    def _send():
        for i in range(0, len(phones), SMS_BATCH):
            batch = phones[i:i + SMS_BATCH]
            try:
                client.send(text, batch, AT_SENDER_ID)
            except Exception:
                log.exception("reminder SMS batch of %d failed", len(batch))

    threading.Thread(target=_send, daemon=True).start()


def due_reminders(now=None):
    """(tenant_id, id) of scheduled reminders whose time has come, across organisations (for the job)."""
    rows = fetch_all("SELECT tenant_id, id FROM reminders WHERE status = 'scheduled' AND send_at <= ? AND deleted_at IS NULL "
                     "ORDER BY send_at LIMIT 200", (now or int(time.time()),))
    return [(r["tenant_id"], r["id"]) for r in rows]
