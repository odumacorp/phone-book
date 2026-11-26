"""Live data for the organisation dashboard: only this organisation's members, never anyone else's."""
import datetime
import re
import time

from pb import stats
from pb.db import fetch_all
from pb.dashboard.models import Card, Series, per_quarter_hour
from pb.tenancy.repo import TenantRepo

CHANNEL_NAMES = {"ussd": "USSD", "android": "Android", "ios": "iPhone", "web": "Web"}
STATUS = {"active": "paid", "trial": "trial", "grace": "due", "suspended": "overdue", "closed": "overdue"}


def _chunks(items, size=400):
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _rows(query, phones, params=()):
    """Run `query` (with {marks} for the phone list) over the phones in chunks. phones None: everyone (the platform)."""
    if phones is None:
        return fetch_all(re.sub(r" AND \w+ IN \(\{marks\}\)", "", query), params)
    out = []
    for chunk in _chunks(phones):
        marks = ", ".join("?" for _ in chunk)
        out += fetch_all(query.format(marks=marks), (*params, *chunk))
    return out


def member_phones(tenant_id, group=None):
    """Active members' phones, optionally only those in one department/group ("d:<id>" / "g:<id>", including inner ones).
    tenant_id None: everyone on the platform (returned as None: no filter)."""
    if tenant_id is None:
        return None
    from pb.org import content
    phones = [m["user_phone"] for m in TenantRepo(tenant_id).all("memberships", "status = 'active'", columns="user_phone")]
    if group:
        tags = content.member_tags(tenant_id)
        phones = [p for p in phones if group in tags.get(p, set())]
    return phones


def _bucket(times, start, end):
    edges = stats.buckets(start, end)
    return [b[0] for b in edges], [sum(1 for t in times if a <= t < b) for _l, a, b in edges]


def ussd_sessions(tenant_id, start, end, group=None, channel=None, label=""):
    phones = member_phones(tenant_id, group)
    rows = [] if channel not in (None, "", "ussd") else _rows(
        "SELECT started_at FROM ussd_sessions WHERE started_at >= ? AND started_at < ? AND owner IN ({marks})", phones, (start, end))
    labels, values = _bucket([r["started_at"] for r in rows], start, end)
    return Card("ussd_sessions", "USSD sessions", label, "line", labels, [Series("Sessions", values)],
                note="USSD only" if channel and channel != "ussd" else "")


def dropoff(tenant_id, start, end, group=None, channel=None, label=""):
    phones = member_phones(tenant_id, group)
    rows = [] if channel not in (None, "", "ussd") else _rows(
        "SELECT started_at, reached_contact FROM ussd_sessions WHERE started_at >= ? AND started_at < ? AND owner IN ({marks})",
        phones, (start, end))
    edges = stats.buckets(start, end)
    values = []
    for _l, a, b in edges:
        inside = [r for r in rows if a <= r["started_at"] < b]
        values.append(round(100 * sum(1 for r in inside if not r["reached_contact"]) / len(inside), 1) if inside else None)
    return Card("dropoff", "Session drop-off rate", label, "line", [e[0] for e in edges], [Series("Dropped before a contact", values)],
                unit="%", data={"max": 100})


def delivery(tenant_id, start, end, group=None, channel=None, label=""):
    phones = member_phones(tenant_id, group)
    where = "tenant_id = ? AND " if tenant_id is not None else ""
    rows = [r for r in fetch_all(
        f"SELECT owner, channel, sent_at, delivered_at, viewed_at FROM deliveries WHERE {where}"
        "(sent_at >= ? AND sent_at < ? OR delivered_at >= ? AND delivered_at < ? OR viewed_at >= ? AND viewed_at < ?)",
        ((tenant_id,) if tenant_id is not None else ()) + (start, end, start, end, start, end))
        if phones is None or r["owner"] in phones]
    if channel == "ussd":
        rows = [r for r in rows if r["channel"] in ("board", "sms")]
    elif channel in ("android", "ios", "web"):
        rows = [r for r in rows if r["channel"] in ("board", "app")]
    labels, sent = _bucket([r["sent_at"] for r in rows], start, end)
    _l, delivered = _bucket([r["delivered_at"] for r in rows if r["delivered_at"]], start, end)
    _l, viewed = _bucket([r["viewed_at"] for r in rows if r["viewed_at"]], start, end)
    return Card("delivery", "Notice delivery", label, "line", labels,
                [Series("Sent", sent), Series("Delivered", delivered), Series("Viewed", viewed)])


def failures(tenant_id, start=None, end=None, group=None, channel=None, label="", now=None):
    """Per minute over the past 24 hours, and the same 24 hours a week earlier."""
    now = now or int(time.time())
    end = now - now % 60 + 60
    start = end - 86400
    phones = member_phones(tenant_id, group)
    week = 7 * 86400
    rows = [] if channel not in (None, "", "ussd") else _rows(
        "SELECT created_at FROM ussd_failures WHERE (created_at >= ? AND created_at < ? OR created_at >= ? AND created_at < ?) "
        "AND caller IN ({marks})", phones, (start, end, start - week, end - week))
    today, last_week = [0] * 1440, [0] * 1440
    for r in rows:
        t = r["created_at"]
        if t >= start:
            today[min(1439, (t - start) // 60)] += 1
        else:
            last_week[min(1439, (t - start + week) // 60)] += 1
    labels = [datetime.datetime.fromtimestamp(start + i * 900, stats.EAT).strftime("%H:%M") for i in range(96)]
    return Card("failures", "Failed sessions", "Past 24 hours", "line", labels,
                [Series("Past 24 hours", per_quarter_hour(today)), Series("Same time last week", per_quarter_hour(last_week))],
                data={"per_minute": {"Past 24 hours": today, "Same time last week": last_week}, "decimals": True},
                note="USSD timeouts and errors per minute (15-minute average)")


def members(tenant_id, start, end, group=None, channel=None, label=""):
    if tenant_id is None:
        return _platform_members(start, end, channel, label)
    repo = TenantRepo(tenant_id)
    phones = member_phones(tenant_id, group)
    keep = set(phones)
    prev = stats.previous(start, end)

    def active(a, b):
        who = set()
        if channel in (None, "", "ussd"):
            who |= {r["owner"] for r in _rows("SELECT owner FROM login_logs WHERE success = 1 AND created_at >= ? AND created_at < ? "
                                                "AND owner IN ({marks})", phones, (a, b))}
        if channel in (None, "", "android", "ios", "web"):
            who |= {r["owner"] for r in _rows("SELECT owner FROM app_sessions WHERE created_at >= ? AND created_at < ? "
                                                "AND owner IN ({marks})", phones, (a, b))}
        return len(who)

    def joined(a, b):
        return sum(1 for m in repo.all("memberships", "joined_at >= ? AND joined_at < ?", (a, b), columns="user_phone")
                   if not group or m["user_phone"] in keep)

    total_before = len(phones) - joined(start, end)
    tiles = [
        dict(key="total", label="Total members", **stats.change(len(phones), max(0, total_before))),
        dict(key="active", label="Active this period", **stats.change(active(start, end), active(*prev))),
        dict(key="new", label="New joins", **stats.change(joined(start, end), joined(*prev))),
    ]
    return Card("members", "Members", label, "kpis", data={"tiles": tiles})


def channel_mix(tenant_id, start, end, group=None, channel=None, label=""):
    phones = member_phones(tenant_id, group)
    rows = _rows("SELECT channel FROM lookups WHERE created_at >= ? AND created_at < ? AND owner IN ({marks})", phones, (start, end))
    counts = {k: 0 for k in CHANNEL_NAMES}
    for r in rows:
        counts[r["channel"] if r["channel"] in counts else "android"] += 1
    if channel:
        counts = {k: (v if k == channel else 0) for k, v in counts.items()}
    keys = [k for k in CHANNEL_NAMES if counts[k] or k != "ios"]  # iPhone only once it's used
    return Card("channel_mix", "Channel mix", label, "donut",
                data={"labels": [CHANNEL_NAMES[k] for k in keys], "values": [counts[k] for k in keys]},
                note="Share of contact lookups")


def top_groups(tenant_id, start, end, group=None, channel=None, label=""):
    if tenant_id is None:
        return _top_organisations(start, end, channel, label)
    from pb.org import content, structure
    tags = content.member_tags(tenant_id)
    places = [(f"d:{d['id']}", d["name"]) for d in structure.departments(tenant_id)] + \
             [(f"g:{x['id']}", x["name"]) for x in structure.groups(tenant_id)]
    if group:
        places = [p for p in places if p[0] == group]
    phones = member_phones(tenant_id)
    lookups = {}
    for r in _rows("SELECT owner, channel FROM lookups WHERE created_at >= ? AND created_at < ? AND owner IN ({marks})",
                   phones, (start, end)):
        if not channel or r["channel"] == channel:
            lookups[r["owner"]] = lookups.get(r["owner"], 0) + 1
    notices = fetch_all("SELECT owner, delivered_at, viewed_at FROM deliveries WHERE tenant_id = ? AND kind = 'notice' "
                        "AND sent_at >= ? AND sent_at < ?", (tenant_id, start, end))
    rows = []
    for key, name in places:
        inside = {p for p in phones if key in tags.get(p, set())}
        if not inside:
            continue
        mine = [n for n in notices if n["owner"] in inside and n["delivered_at"]]
        read = round(100 * sum(1 for n in mine if n["viewed_at"]) / len(mine)) if mine else None
        rows.append({"name": name, "members": len(inside), "lookups": sum(lookups.get(p, 0) for p in inside), "read_rate": read})
    rows.sort(key=lambda r: (-r["lookups"], -r["members"], r["name"].lower()))
    return Card("top_groups", "Top groups", label, "table", data={"rows": rows[:10]})


def billing(tenant_id, start=None, end=None, group=None, channel=None, label="", now=None):
    if tenant_id is None:
        return _revenue(label)
    from pb.billing import catalogue
    from pb.tenancy import tenants as tenancy
    t = tenancy.tenant(tenant_id)
    plan = catalogue.plan(t["plan_key"]) if t and t["plan_key"] else None
    count = len(member_phones(tenant_id))
    quote = catalogue.quote(plan, count, catalogue.plan_tiers(plan["key"])) if plan else None
    today = datetime.datetime.fromtimestamp(now or time.time(), stats.EAT).date()
    next_due = (today.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    return Card("billing", "Billing", "Current plan", "billing", data={
        "plan": plan["name"] if plan else "No plan", "members": count,
        "amount_cents": quote["total"] if quote else None, "per": quote["per"] if quote else None,
        "next_payment": next_due.isoformat(), "status": STATUS.get(t["status"] if t else "", "due"),
        "mpesa_connected": False,
    }, note="M-Pesa payments aren't connected yet: the amount is worked out from your plan and member count.")


def _platform_members(start, end, channel, label):
    prev = stats.previous(start, end)

    def active(a, b):
        who = set()
        if channel in (None, "", "ussd"):
            who |= {r["owner"] for r in fetch_all("SELECT owner FROM login_logs WHERE success = 1 AND created_at >= ? AND created_at < ?", (a, b))}
        if channel in (None, "", "android", "ios", "web"):
            who |= {r["owner"] for r in fetch_all("SELECT owner FROM app_sessions WHERE created_at >= ? AND created_at < ?", (a, b))}
        return len(who)

    def joined(a, b):
        return stats._count("SELECT COUNT(*) AS n FROM users WHERE created_at >= ? AND created_at < ?", (a, b))

    total = stats._count("SELECT COUNT(*) AS n FROM users", ())
    tiles = [dict(key="total", label="Accounts", **stats.change(total, total - joined(start, end))),
             dict(key="active", label="Active this period", **stats.change(active(start, end), active(*prev))),
             dict(key="new", label="New accounts", **stats.change(joined(start, end), joined(*prev)))]
    return Card("members", "People", label, "kpis", data={"tiles": tiles})


def _top_organisations(start, end, channel, label):
    by_org = stats.members_by_organisation()
    lookups = {}
    for r in fetch_all("SELECT owner, channel FROM lookups WHERE created_at >= ? AND created_at < ?", (start, end)):
        if not channel or r["channel"] == channel:
            lookups[r["owner"]] = lookups.get(r["owner"], 0) + 1
    notices = fetch_all("SELECT tenant_id, delivered_at, viewed_at FROM deliveries WHERE kind = 'notice' AND sent_at >= ? AND sent_at < ?",
                        (start, end))
    rows = []
    for org in stats.organisations():
        phones = by_org.get(org["id"], [])
        mine = [n for n in notices if n["tenant_id"] == org["id"] and n["delivered_at"]]
        rows.append({"name": org["name"], "id": org["id"], "members": len(phones), "lookups": sum(lookups.get(p, 0) for p in phones),
                     "read_rate": round(100 * sum(1 for n in mine if n["viewed_at"]) / len(mine)) if mine else None})
    rows.sort(key=lambda r: (-r["lookups"], -r["members"], r["name"].lower()))
    return Card("top_groups", "Top organisations", label, "table", data={"rows": rows[:10], "first": "Organisation",
                                                                         "link": "/platform/org/{id}/panel"})


def _revenue(label):
    """Monthly recurring revenue from every organisation's plan and member count, and who is due or overdue."""
    from pb.billing import catalogue
    by_org = stats.members_by_organisation()
    plans = {}
    total = 0
    attention = []
    paying = 0
    for org in stats.organisations():
        if org["status"] == "closed" or not org["plan_key"]:
            continue
        plan = catalogue.plan(org["plan_key"])
        if not plan:
            continue
        q = catalogue.quote(plan, len(by_org.get(org["id"], [])), catalogue.plan_tiers(plan["key"]), interval="month")
        monthly = q["per_month"] if q and q["per_month"] is not None else 0
        if org["status"] in ("active", "grace"):
            total += monthly
            paying += 1
            plans[plan["name"]] = plans.get(plan["name"], 0) + monthly
        if org["status"] in ("trial", "grace", "suspended"):
            attention.append({"name": org["name"], "id": org["id"], "status": STATUS.get(org["status"], org["status"]),
                              "amount_cents": monthly})
    return Card("billing", "Revenue", "Every month, from current plans", "revenue", data={
        "mrr_cents": total, "paying": paying, "by_plan": sorted(plans.items(), key=lambda kv: -kv[1]),
        "attention": attention[:8], "mpesa_connected": False,
    }, note="Worked out from each organisation's plan and members; M-Pesa isn't connected yet.")


def organisations(tenant_id, start, end, group=None, channel=None, label=""):
    """Platform only: organisations now, new ones in the period, and those that need a decision."""
    orgs = stats.organisations()
    prev = stats.previous(start, end)
    new = sum(1 for o in orgs if start <= o["created_at"] < end)
    new_before = sum(1 for o in orgs if prev[0] <= o["created_at"] < prev[1])
    live = [o for o in orgs if o["status"] in ("trial", "active", "grace")]
    check = [o for o in orgs if o["status"] in ("trial", "grace", "suspended")]
    tiles = [dict(key="total", label="Organisations", **stats.change(len(live), len(live) - new)),
             dict(key="new", label="New organisations", **stats.change(new, new_before)),
             dict(key="check", label="Trial, grace or suspended", **stats.change(len(check), len(check)))]
    return Card("organisations", "Organisations", label, "kpis", data={"tiles": tiles})


CARDS = {
    "organisations": organisations,
    "ussd_sessions": ussd_sessions, "dropoff": dropoff, "delivery": delivery, "failures": failures,
    "members": members, "channel_mix": channel_mix, "top_groups": top_groups, "billing": billing,
}
