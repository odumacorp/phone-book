import calendar
import datetime
import hmac
import re
import secrets
import time
from flask import abort, redirect, render_template, request, session, url_for

from pb.api.complaints import COMPLAINT_MAX, client_ip
from pb.config import ADMIN_PASSWORD, EMAIL_FROM, LOCKOUT_SECONDS, SECRET_KEY, log
from pb.crypto import CONTACTS_CIPHER, convert_contacts
from pb.db import execute, fetch_all, fetch_one
from pb.features import FEATURES, feature_on, set_feature
from pb.notify import COMMON_VARS, EMAIL_PARTS, LOG_KINDS, NONE, NOTIFICATION_LOG_DAYS, SAMPLE_VARS, TEMPLATES, email_configured, email_part, log_notification, notify, notify_event, render_message, send_email, send_emails_async, set_setting_text, setting_text, template_on, template_part
from pb.text import local_number, login_phone
from pb.users import clean_email, get_user
from pb.web import app


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
