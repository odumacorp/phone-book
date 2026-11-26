"""Demo data for the organisation dashboard (DASHBOARD_DATA=mock): realistic Kenyan volumes with a weekend dip.

Deterministic: the same organisation and dates always give the same numbers, so screenshots and tests are stable.
"""
import datetime
import random

from pb import stats
from pb.dashboard.models import Card, Series, per_quarter_hour

GROUPS = ["Form 4 West", "Form 2 East", "Teaching staff", "Boarding", "Parents' Association", "Finance office",
          "Transport", "Sports", "Administration", "Form 1 North"]
NAMES = ["Wanjiku Kamau", "Otieno Odhiambo", "Achieng Atieno", "Kiprotich Langat", "Mwangi Njoroge", "Fatuma Hassan",
         "Chebet Jeptoo", "Mutua Kioko", "Nduta Gichuru", "Omondi Were", "Amina Abdi", "Wafula Simiyu"]


def _rng(tenant_id, *key):
    return random.Random(f"{tenant_id}:{':'.join(str(k) for k in key)}")


def _weekday_factor(ts):
    day = datetime.datetime.fromtimestamp(ts, stats.EAT).weekday()
    return 0.45 if day == 6 else 0.6 if day == 5 else 1.0  # Sunday quietest, Saturday next


def _per_bucket(tenant_id, start, end, base, name):
    labels, values = [], []
    for label, a, b in stats.buckets(start, end):
        days = max(1, round((b - a) / 86400))
        r = _rng(tenant_id, name, a)
        values.append(int(sum(base * _weekday_factor(a + d * 86400) * r.uniform(0.8, 1.2) for d in range(days))))
        labels.append(label)
    return labels, values


def ussd_sessions(tenant_id, start, end, group=None, channel=None, label=""):
    labels, values = _per_bucket(tenant_id, start, end, 140 if not group else 30, "sessions")
    return Card("ussd_sessions", "USSD sessions", label, "line", labels, [Series("Sessions", values)], source="demo")


def dropoff(tenant_id, start, end, group=None, channel=None, label=""):
    labels = [b[0] for b in stats.buckets(start, end)]
    values = [round(_rng(tenant_id, "drop", b[1]).uniform(9, 22) + (4 if _weekday_factor(b[1]) < 1 else 0), 1)
              for b in stats.buckets(start, end)]
    return Card("dropoff", "Session drop-off rate", label, "line", labels, [Series("Dropped before a contact", values)],
                unit="%", data={"max": 100}, source="demo")


def delivery(tenant_id, start, end, group=None, channel=None, label=""):
    labels, sent = _per_bucket(tenant_id, start, end, 220 if not group else 45, "sent")
    delivered = [int(v * _rng(tenant_id, "dl", i).uniform(0.9, 0.98)) for i, v in enumerate(sent)]
    viewed = [int(v * _rng(tenant_id, "vw", i).uniform(0.45, 0.7)) for i, v in enumerate(delivered)]
    return Card("delivery", "Notice delivery", label, "line", labels,
                [Series("Sent", sent), Series("Delivered", delivered), Series("Viewed", viewed)], source="demo")


def failures(tenant_id, start=None, end=None, group=None, channel=None, label="", now=None):
    now = now or int(datetime.datetime.now(stats.EAT).timestamp())
    end = now - now % 60 + 60
    begin = end - 86400
    labels, today, last_week = [], [], []
    for i in range(1440):
        t = begin + i * 60
        hour = datetime.datetime.fromtimestamp(t, stats.EAT).hour
        busy = 1.0 if 7 <= hour <= 21 else 0.15
        r = _rng(tenant_id, "fail", t // 60)
        today.append(1 if r.random() < 0.035 * busy else 0)
        last_week.append(1 if r.random() < 0.045 * busy else 0)
    labels = [datetime.datetime.fromtimestamp(begin + i * 900, stats.EAT).strftime("%H:%M") for i in range(96)]
    return Card("failures", "Failed sessions", "Past 24 hours", "line", labels,
                [Series("Past 24 hours", per_quarter_hour(today)), Series("Same time last week", per_quarter_hour(last_week))],
                data={"per_minute": {"Past 24 hours": today, "Same time last week": last_week}, "decimals": True},
                note="USSD timeouts and errors per minute (15-minute average)", source="demo")


def members(tenant_id, start, end, group=None, channel=None, label=""):
    r = _rng(tenant_id, "members", start)
    total = 1240 if not group else 86
    if tenant_id is None:
        total = 18450
    days = max(1, (end - start) // 86400)
    active, active_before = int(total * min(0.9, 0.12 * days ** 0.5)), int(total * min(0.88, 0.11 * days ** 0.5))
    new, new_before = max(1, int(days * r.uniform(1.5, 3))), max(1, int(days * r.uniform(1.2, 2.8)))
    platform = tenant_id is None
    tiles = [dict(key="total", label="Accounts" if platform else "Total members", **stats.change(total, total - new)),
             dict(key="active", label="Active this period", **stats.change(active, active_before)),
             dict(key="new", label="New accounts" if platform else "New joins", **stats.change(new, new_before))]
    return Card("members", "People" if tenant_id is None else "Members", label, "kpis", data={"tiles": tiles}, source="demo")


def channel_mix(tenant_id, start, end, group=None, channel=None, label=""):
    days = max(1, (end - start) // 86400)
    values = [int(days * 310), int(days * 95), int(days * 40)]
    labels = ["USSD", "Android", "Web"]
    if channel:
        keep = {"ussd": "USSD", "android": "Android", "web": "Web"}.get(channel)
        values = [v if l == keep else 0 for l, v in zip(labels, values)]
    return Card("channel_mix", "Channel mix", label, "donut", data={"labels": labels, "values": values},
                note="Share of contact lookups", source="demo")


ORGS = ["Kilimani Hills Academy", "Ufanisi Teachers SACCO", "Savannah Logistics Ltd", "Wanjiru, Okafor & Patel Advocates",
        "Lakeview Girls' High School", "Mkulima Dairy Farmers SACCO", "Baraka Supermarkets Ltd", "Afya Health Workers SACCO",
        "Tausi Hotels & Resorts", "Mombasa Coast Academy"]


def top_groups(tenant_id, start, end, group=None, channel=None, label=""):
    days = max(1, (end - start) // 86400)
    if tenant_id is None:
        rows = []
        for i, name in enumerate(ORGS):
            r = _rng(0, "org", name)
            members = r.randint(120, 2400)
            rows.append({"name": name, "id": i + 1, "members": members, "lookups": int(members * days * r.uniform(0.15, 0.5)),
                         "read_rate": r.randint(45, 90)})
        rows.sort(key=lambda x: -x["lookups"])
        return Card("top_groups", "Top organisations", label, "table", data={"rows": rows, "first": "Organisation"}, source="demo")
    rows = []
    for i, name in enumerate(GROUPS):
        r = _rng(tenant_id, "group", name)
        members = r.randint(18, 140)
        rows.append({"name": name, "members": members, "lookups": int(members * days * r.uniform(0.2, 0.7)),
                     "read_rate": r.randint(41, 92)})
    rows.sort(key=lambda x: -x["lookups"])
    return Card("top_groups", "Top groups", label, "table", data={"rows": rows}, source="demo")


def billing(tenant_id, start=None, end=None, group=None, channel=None, label="", now=None):
    if tenant_id is None:
        return Card("billing", "Revenue", "Every month, from current plans", "revenue", data={
            "mrr_cents": 41_870_000, "paying": 37, "mpesa_connected": True,
            "by_plan": [("Enterprise Standard", 21_400_000), ("Enterprise Pro", 14_200_000), ("Enterprise Starter", 6_270_000)],
            "attention": [{"name": ORGS[4], "id": 5, "status": "trial", "amount_cents": 750_000},
                          {"name": ORGS[8], "id": 9, "status": "due", "amount_cents": 1_120_000},
                          {"name": ORGS[9], "id": 10, "status": "overdue", "amount_cents": 312_500}]}, source="demo")
    today = datetime.datetime.now(stats.EAT).date()
    next_due = (today.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    return Card("billing", "Billing", "Current plan", "billing", data={
        "plan": "Enterprise Standard", "members": 1240, "amount_cents": 750000 + 990 * 2500, "per": "month",
        "next_payment": next_due.isoformat(), "status": "paid", "mpesa_connected": True,
        "last_payment": {"mpesa_ref": "SKL4H7Q2XM", "amount_cents": 750000 + 990 * 2500, "paid_on": today.replace(day=1).isoformat(),
                         "payer": NAMES[0]},
    }, source="demo")


def organisations(tenant_id, start, end, group=None, channel=None, label=""):
    days = max(1, (end - start) // 86400)
    tiles = [dict(key="total", label="Organisations", **stats.change(41, 41 - max(1, days // 7))),
             dict(key="new", label="New organisations", **stats.change(max(1, days // 7), max(1, days // 9))),
             dict(key="check", label="Trial, grace or suspended", **stats.change(6, 5))]
    return Card("organisations", "Organisations", label, "kpis", data={"tiles": tiles}, source="demo")


CARDS = {
    "organisations": organisations,
    "ussd_sessions": ussd_sessions, "dropoff": dropoff, "delivery": delivery, "failures": failures,
    "members": members, "channel_mix": channel_mix, "top_groups": top_groups, "billing": billing,
}
