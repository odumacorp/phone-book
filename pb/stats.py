"""Statistics dashboards: the platform's (Oduma Corp staff, every organisation) and one organisation's (its admins).

Each figure is counted for a chosen period and the period before it (same length), so the dashboard can show the change.
Periods are in Kenya time (EAT, UTC+3). Platform-wide figures read across organisations on purpose (like the
Organisations list); an organisation's figures go through TenantRepo and only ever see that organisation.
"""
import datetime
import time

from pb.db import fetch_all, fetch_one
from pb.tenancy.repo import TenantRepo

EAT = datetime.timezone(datetime.timedelta(hours=3))
RANGES = {  # key: label (the buttons, in order)
    "today": "Today", "yesterday": "Yesterday", "7d": "Last 7 days", "30d": "Last 30 days", "3m": "Last 3 months",
    "6m": "Last 6 months", "year": "This year", "all": "All time",
}
FIRST_DAY = datetime.date(2026, 1, 1)  # "All time" starts here (before any data)


def _midnight(day):
    return int(datetime.datetime.combine(day, datetime.time(0, 0), EAT).timestamp())


def period(key="30d", start=None, end=None, now=None):
    """(key, label, start, end) as epoch seconds [start, end). key "custom" uses start/end dates (YYYY-MM-DD)."""
    now = now or int(time.time())
    today = datetime.datetime.fromtimestamp(now, EAT).date()
    tomorrow = _midnight(today + datetime.timedelta(days=1))
    if key == "custom":
        try:
            a = datetime.date.fromisoformat(str(start))
            b = datetime.date.fromisoformat(str(end))
        except ValueError:
            key = "30d"
        else:
            if b < a:
                a, b = b, a
            return "custom", f"{a:%d %b %Y} to {b:%d %b %Y}", _midnight(a), _midnight(b + datetime.timedelta(days=1))
    days = {"today": 1, "7d": 7, "30d": 30, "3m": 91, "6m": 182}
    if key == "yesterday":
        return key, RANGES[key], _midnight(today - datetime.timedelta(days=1)), _midnight(today)
    if key == "year":
        return key, RANGES[key], _midnight(datetime.date(today.year, 1, 1)), tomorrow
    if key == "all":
        return key, RANGES[key], _midnight(FIRST_DAY), tomorrow
    key = key if key in days else "30d"
    return key, RANGES[key], _midnight(today - datetime.timedelta(days=days[key] - 1)), tomorrow


def previous(start, end):
    return start - (end - start), start


def change(now_value, before):
    """{"value", "before", "pct" (None when there's nothing to compare), "dir": "up" | "down" | "same"}."""
    pct = None if not before else round((now_value - before) * 100 / before)
    return {"value": now_value, "before": before, "pct": pct,
            "dir": "up" if now_value > before else "down" if now_value < before else "same"}


def buckets(start, end):
    """[(label, start, end)] for the activity chart: days up to ~2 months, then weeks, then months."""
    span_days = max(1, (end - start) // 86400)
    out, day = [], datetime.datetime.fromtimestamp(start, EAT).date()
    last = datetime.datetime.fromtimestamp(end - 1, EAT).date()
    if span_days <= 62:
        while day <= last:
            out.append((f"{day:%d %b}", _midnight(day), _midnight(day + datetime.timedelta(days=1))))
            day += datetime.timedelta(days=1)
    elif span_days <= 200:
        while day <= last:
            nxt = min(day + datetime.timedelta(days=7), last + datetime.timedelta(days=1))
            out.append((f"w/c {day:%d %b}", _midnight(day), _midnight(nxt)))
            day = nxt
    else:
        day = day.replace(day=1)
        while day <= last:
            nxt = (day.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
            out.append((f"{day:%b %Y}", max(start, _midnight(day)), min(end, _midnight(nxt))))
            day = nxt
    return out


def _count(query, params):
    row = fetch_one(query, params)
    return (row["n"] if row else 0) or 0


def _series(times, start, end):
    """Bucket a list of epoch times for the chart: [{"label", "value"}]."""
    rows = []
    for label, a, b in buckets(start, end):
        rows.append({"label": label, "value": sum(1 for t in times if a <= t < b)})
    return rows


# ---------------------------------------------------------------- the Admin panel's tabs and quick actions

def platform_panel():
    """Staff: tabs with counts, organisations that need a decision, and the newest accounts (to turn off or on)."""
    return {
        "tabs": [
            ("People", "/platform/people", _count("SELECT COUNT(*) AS n FROM users", ())),
            ("Organisations", "/platform/tenants", _count("SELECT COUNT(*) AS n FROM tenants", ())),
            ("Test accounts", "/platform/test-accounts", _count("SELECT COUNT(*) AS n FROM users WHERE is_test = 1", ())),
            ("Prices", "/platform/pricing", _count("SELECT COUNT(*) AS n FROM plans WHERE active = 1", ())),
            ("USSD codes", "/platform/ussd-codes", _count("SELECT COUNT(*) AS n FROM ussd_codes", ())),
            ("Staff", "/platform/staff", _count("SELECT COUNT(*) AS n FROM staff_users WHERE active = 1", ())),
            ("Activity log", "/platform/audit", None),
        ],
        "orgs_to_check": [dict(r) for r in fetch_all(
            "SELECT id, name, status, created_at FROM tenants WHERE status IN ('trial', 'grace', 'suspended') "
            "ORDER BY created_at DESC LIMIT 8")],
        "newest_accounts": [dict(r) for r in fetch_all(
            "SELECT phone, name, created_at, disabled, is_test FROM users ORDER BY COALESCE(created_at, 0) DESC, phone LIMIT 8")],
    }


def org_panel(tenant_id, areas):
    """An organisation's admins: tabs with counts (only the pages they can use), join requests and the newest members."""
    repo = TenantRepo(tenant_id)
    base = f"/platform/org/{tenant_id}"
    tabs = [
        ("members", "Members", f"{base}/members", repo.count("memberships", "status = 'active'")),
        ("members", "Join requests", f"{base}/members?tab=requests", repo.count("memberships", "status = 'pending'")),
        ("notices", "Notices", f"{base}/notices", repo.count("notices", "deleted_at IS NULL")),
        ("contacts", "Contacts", f"{base}/contacts", repo.count("org_contacts", "deleted_at IS NULL AND is_emergency = 0")),
        ("emergency", "Emergency contacts", f"{base}/emergency", repo.count("org_contacts", "deleted_at IS NULL AND is_emergency = 1")),
        ("contacts", "Contact groups", f"{base}/contact-groups", repo.count("contact_groups")),
        ("structure", "Groups and departments", f"{base}/groups", repo.count("departments") + repo.count("member_groups")),
        ("reminders", "Reminders", f"{base}/reminders", repo.count("reminders", "deleted_at IS NULL")),
        ("settings", "Settings", f"{base}/settings", None),
    ]
    return {
        "tabs": [(label, href, n) for area, label, href, n in tabs if area in areas],
        "requests": [dict(r) for r in repo.all("memberships", "status = 'pending'", order="joined_at", limit=10)],
        "newest_members": [dict(r) for r in repo.all("memberships", "status = 'active'", order="joined_at DESC", limit=8)],
    }


def members_by_organisation():
    """{tenant_id: [phones of active members]} for every organisation (the platform dashboard; staff only)."""
    out = {}
    for r in fetch_all("SELECT tenant_id, user_phone FROM memberships WHERE status = 'active'"):
        out.setdefault(r["tenant_id"], []).append(r["user_phone"])
    return out


def organisations():
    return [dict(r) for r in fetch_all("SELECT id, name, edition, plan_key, status, created_at FROM tenants ORDER BY name")]
