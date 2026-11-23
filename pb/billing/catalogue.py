"""Plans, plan features, volume tiers, add-ons and USSD code fees: every price lives here, in the database.

Money is integer cents (KES x 100). The Super Admin edits all of it at /platform/pricing; every change is audited
with the old and new values.
"""
import re
import time

from pb.audit import audit
from pb.db import execute, fetch_all, fetch_one, insert_id

MODELS = {
    "per_transaction": "Per transaction",
    "recurring": "Recurring subscription",
    "seat": "Monthly + per member",
    "quoted": "Quoted",
}
INTERVALS = {"": "-", "month": "Monthly", "year": "Annual"}
AUDIENCES = {"individual": "Individual", "enterprise": "Enterprise"}
CODE_TYPES = {"shared": "Shared code (org code)", "extension": "Dedicated extension", "dedicated": "Dedicated shortcode"}
FEATURE_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
PLAN_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")


def kes(cents):
    """2500000 -> "KES 25,000"; 4950 -> "KES 49.50"; None -> "-"."""
    if cents is None:
        return "-"
    whole, part = divmod(int(cents), 100)
    return f"KES {whole:,}" + (f".{part:02d}" if part else "")


def parse_kes(text, allow_empty=False):
    """ "25,000" / "KES 25000" / "49.50" -> cents. Raises ValueError for anything else."""
    text = str(text or "").strip().upper().replace("KES", "").replace(",", "").strip()
    if not text:
        if allow_empty:
            return None
        raise ValueError("Enter an amount.")
    if not re.match(r"^\d{1,9}(\.\d{1,2})?$", text):
        raise ValueError(f"'{text}' isn't an amount in KES.")
    whole, _, part = text.partition(".")
    return int(whole) * 100 + int((part + "00")[:2])


def parse_int(text, allow_empty=True, low=0, high=10_000_000):
    text = str(text or "").strip().replace(",", "")
    if not text:
        if allow_empty:
            return None
        raise ValueError("Enter a number.")
    if not text.isdigit() or not low <= int(text) <= high:
        raise ValueError(f"'{text}' isn't a whole number between {low} and {high:,}.")
    return int(text)


# ---------------------------------------------------------------- plans

PLAN_FIELDS = (
    "key, name, audience, model, billing_interval, base_price, included_members, per_member_price, "
    "annual_discount_pct, trial_days, active, sort, updated_at, updated_by"
)


def plans(audience=None, active_only=False):
    where, params = [], []
    if audience:
        where.append("audience = ?")
        params.append(audience)
    if active_only:
        where.append("active = 1")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return fetch_all(f"SELECT {PLAN_FIELDS} FROM plans {clause} ORDER BY sort, key", tuple(params))


def plan(key):
    return fetch_one(f"SELECT {PLAN_FIELDS} FROM plans WHERE key = ?", (key,))


def plan_features(key):
    return {r["feature"]: r["value"] for r in fetch_all("SELECT feature, value FROM plan_features WHERE plan_key = ? ORDER BY feature", (key,))}


def plan_feature(key, feature, default=None):
    row = fetch_one("SELECT value FROM plan_features WHERE plan_key = ? AND feature = ?", (key, feature))
    return row["value"] if row else default


def plan_tiers(key):
    return fetch_all("SELECT id, from_members, per_member_price FROM plan_tiers WHERE plan_key = ? ORDER BY from_members", (key,))


def _changes(old, new):
    return {k: [old.get(k), v] for k, v in new.items() if old.get(k) != v}


def update_plan(key, actor, **values):
    """Validated values only (see pb/portal/pricing.py). Returns the changed fields."""
    old = plan(key)
    if not old:
        raise ValueError("No such plan.")
    allowed = {"name", "model", "billing_interval", "base_price", "included_members", "per_member_price",
               "annual_discount_pct", "trial_days", "active", "sort"}
    values = {k: v for k, v in values.items() if k in allowed}
    changes = _changes(dict(old), values)
    if changes:
        sets = ", ".join(f"{k} = ?" for k in values)
        execute(f"UPDATE plans SET {sets}, updated_at = ?, updated_by = ? WHERE key = ?", (*values.values(), int(time.time()), actor, key))
        audit("pricing.plan.update", None, ("plan", key), changes, actor=actor)
    return changes


def create_plan(actor, key, name, audience, model):
    if not PLAN_KEY_RE.match(key):
        raise ValueError("Plan key: lower-case letters, digits and _ (3-40 characters).")
    if plan(key):
        raise ValueError("A plan with this key already exists.")
    if audience not in AUDIENCES or model not in MODELS:
        raise ValueError("Choose the audience and pricing model.")
    execute(
        "INSERT INTO plans (key, name, audience, model, active, sort, updated_at, updated_by) VALUES (?, ?, ?, ?, 0, 100, ?, ?)",
        (key, name, audience, model, int(time.time()), actor),
    )
    audit("pricing.plan.create", None, ("plan", key), {"name": name, "audience": audience, "model": model}, actor=actor)


def set_plan_feature(key, feature, value, actor):
    if not FEATURE_RE.match(feature):
        raise ValueError("Feature names: lower-case letters, digits and _.")
    old = plan_feature(key, feature)
    if value is None:
        execute("DELETE FROM plan_features WHERE plan_key = ? AND feature = ?", (key, feature))
    else:
        execute(
            "INSERT INTO plan_features (plan_key, feature, value) VALUES (?, ?, ?) "
            "ON CONFLICT (plan_key, feature) DO UPDATE SET value = excluded.value",
            (key, feature, value),
        )
    if old != value:
        audit("pricing.plan.feature", None, ("plan", key), {feature: [old, value]}, actor=actor)


def add_tier(key, from_members, per_member_price, actor):
    tier_id = insert_id(
        "INSERT INTO plan_tiers (plan_key, from_members, per_member_price) VALUES (?, ?, ?)", (key, from_members, per_member_price)
    )
    audit("pricing.plan.tier_add", None, ("plan", key), {"from_members": from_members, "per_member_price": per_member_price}, actor=actor)
    return tier_id


def delete_tier(key, tier_id, actor):
    execute("DELETE FROM plan_tiers WHERE plan_key = ? AND id = ?", (key, tier_id))
    audit("pricing.plan.tier_delete", None, ("plan", key), {"tier_id": tier_id}, actor=actor)


# ---------------------------------------------------------------- add-ons

def addons(audience=None):
    clause, params = ("WHERE audience = ?", (audience,)) if audience else ("", ())
    return fetch_all(
        f"SELECT key, name, audience, price, billing_interval, module_key, active, updated_at, updated_by FROM addons {clause} ORDER BY audience, name",
        params,
    )


def addon(key):
    return fetch_one("SELECT key, name, audience, price, billing_interval, module_key, active, updated_at, updated_by FROM addons WHERE key = ?", (key,))


def update_addon(key, actor, **values):
    old = addon(key)
    if not old:
        raise ValueError("No such add-on.")
    values = {k: v for k, v in values.items() if k in {"name", "price", "billing_interval", "active"}}
    changes = _changes(dict(old), values)
    if changes:
        sets = ", ".join(f"{k} = ?" for k in values)
        execute(f"UPDATE addons SET {sets}, updated_at = ?, updated_by = ? WHERE key = ?", (*values.values(), int(time.time()), actor, key))
        audit("pricing.addon.update", None, ("addon", key), changes, actor=actor)
    return changes


# ---------------------------------------------------------------- USSD code fees

def code_fees():
    return fetch_all("SELECT code_type, name, setup_fee, monthly_fee, note, updated_at, updated_by FROM ussd_code_fees ORDER BY setup_fee")


def code_fee(code_type):
    return fetch_one("SELECT code_type, name, setup_fee, monthly_fee, note, updated_at, updated_by FROM ussd_code_fees WHERE code_type = ?", (code_type,))


def update_code_fee(code_type, actor, **values):
    old = code_fee(code_type)
    if not old:
        raise ValueError("No such code type.")
    values = {k: v for k, v in values.items() if k in {"name", "setup_fee", "monthly_fee", "note"}}
    changes = _changes(dict(old), values)
    if changes:
        sets = ", ".join(f"{k} = ?" for k in values)
        execute(f"UPDATE ussd_code_fees SET {sets}, updated_at = ?, updated_by = ? WHERE code_type = ?", (*values.values(), int(time.time()), actor, code_type))
        audit("pricing.code_fee.update", None, ("code_fee", code_type), changes, actor=actor)
    return changes


def price_list():
    """Every price item in one list, for the Super Admin's pricing page."""
    items = []
    for p in plans():
        if p["model"] == "quoted":
            price = "Quoted"
        elif p["model"] == "seat":
            price = f"{kes(p['base_price'])}/month incl. {p['included_members'] or 0} members, then {kes(p['per_member_price'])}/member"
        elif p["model"] == "recurring":
            price = f"{kes(p['base_price'])}/{'year' if p['billing_interval'] == 'year' else 'month'}"
        else:
            price = f"{kes(p['base_price'])} each"
        items.append({"kind": "plan", "key": p["key"], "name": p["name"], "group": AUDIENCES.get(p["audience"], p["audience"]),
                      "price": price, "active": bool(p["active"]), "updated_at": p["updated_at"], "updated_by": p["updated_by"]})
    for a in addons():
        price = f"{kes(a['price'])}/{a['billing_interval'] or 'once'}" if a["price"] else "Price not set"
        items.append({"kind": "addon", "key": a["key"], "name": a["name"], "group": f"Add-on ({AUDIENCES.get(a['audience'], a['audience'])})",
                      "price": price, "active": bool(a["active"]), "updated_at": a["updated_at"], "updated_by": a["updated_by"]})
    for f in code_fees():
        price = f"Setup {kes(f['setup_fee'])}, then {kes(f['monthly_fee'])}/month" if (f["setup_fee"] or f["monthly_fee"]) else "Included in plan"
        items.append({"kind": "code_fee", "key": f["code_type"], "name": f["name"], "group": "USSD code fee",
                      "price": price, "active": True, "updated_at": f["updated_at"], "updated_by": f["updated_by"]})
    return items
