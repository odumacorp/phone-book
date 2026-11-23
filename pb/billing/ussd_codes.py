"""USSD codes: the inventory of codes Oduma Corp has bought, and their allocation to organisations.

- The Super Admin adds a code after buying it (shared channel, dedicated extension, or dedicated shortcode).
- One code is the *main* code: today's personal phone book and every shared-code organisation live under it.
  It starts as *384*88041# (the test channel) and can be changed from the inventory.
- Shared codes host many organisations, each with its own org code (*384*88041*<org code>#).
  Extensions and dedicated shortcodes belong to one organisation at a time.
- Assigning copies the current setup/monthly fee for that code type onto the allocation (the invoice line in Phase 3).
  Assigning a new code ends the organisation's previous allocation (proration comes with billing).

Allocation queries here span tenants on purpose (the platform inventory view), always with explicit filters.
"""
import re
import secrets
import time

from pb.audit import audit
from pb.billing.catalogue import CODE_TYPES, code_fee
from pb.db import execute, fetch_all, fetch_one, insert_id
from pb.features import SETTINGS_TTL

CODE_RE = re.compile(r"^\*\d{2,6}(\*\d{1,6}){0,3}#$")
ORG_CODE_RE = re.compile(r"^\d{3,6}$")
FALLBACK_MAIN = "*384*88041#"
_main_cache = {}


def normalise_code(raw):
    code = str(raw or "").replace(" ", "").strip()
    return code if CODE_RE.match(code) else None


def codes(status=None):
    clause, params = ("WHERE c.status = ?", (status,)) if status else ("", ())
    return fetch_all(
        "SELECT c.id, c.code, c.code_type, c.provider, c.status, c.is_main, c.monthly_cost, c.note, c.created_at, "
        "(SELECT COUNT(*) FROM ussd_code_allocations a WHERE a.code_id = c.id AND a.status = 'active') AS tenants "
        f"FROM ussd_codes c {clause} ORDER BY c.is_main DESC, c.code_type, c.code",
        params,
    )


def code(code_id):
    return fetch_one("SELECT id, code, code_type, provider, status, is_main, monthly_cost, note FROM ussd_codes WHERE id = ?", (code_id,))


def main_code():
    """The main USSD code as users dial it (cached for a few seconds, like feature switches)."""
    cached = _main_cache.get("main")
    if cached and time.time() - cached[1] < SETTINGS_TTL:
        return cached[0]
    try:
        row = fetch_one("SELECT code FROM ussd_codes WHERE is_main = 1")
    except Exception:  # before migrations have run
        row = None
    value = row["code"] if row else FALLBACK_MAIN
    _main_cache["main"] = (value, time.time())
    return value


def add_code(actor, raw_code, code_type, provider="", note="", monthly_cost=None):
    code_text = normalise_code(raw_code)
    if not code_text:
        raise ValueError("Enter the code as dialled, e.g. *384*88041# or *384*88041*5#.")
    if code_type not in CODE_TYPES:
        raise ValueError("Choose the code type.")
    if fetch_one("SELECT 1 AS x FROM ussd_codes WHERE code = ?", (code_text,)):
        raise ValueError(f"{code_text} is already in the list.")
    code_id = insert_id(
        "INSERT INTO ussd_codes (code, code_type, provider, status, is_main, monthly_cost, note, added_by, created_at) "
        "VALUES (?, ?, ?, 'available', 0, ?, ?, ?, ?)",
        (code_text, code_type, provider.strip()[:60] or None, monthly_cost, note.strip()[:200] or None, actor, int(time.time())),
    )
    audit("ussd_code.add", None, ("ussd_code", code_id), {"code": code_text, "type": code_type, "provider": provider}, actor=actor)
    return code_id


def set_main(actor, code_id):
    row = code(code_id)
    if not row or row["code_type"] != "shared" or row["status"] == "retired":
        raise ValueError("The main code must be a shared code that isn't retired.")
    old = fetch_one("SELECT code FROM ussd_codes WHERE is_main = 1")
    execute("UPDATE ussd_codes SET is_main = 0 WHERE is_main = 1")
    execute("UPDATE ussd_codes SET is_main = 1, status = 'in_use' WHERE id = ?", (code_id,))
    _main_cache.clear()
    audit("ussd_code.set_main", None, ("ussd_code", code_id), {"from": old["code"] if old else None, "to": row["code"]}, actor=actor)


def retire(actor, code_id):
    row = code(code_id)
    if not row:
        raise ValueError("No such code.")
    if row["is_main"]:
        raise ValueError("Choose another main code before retiring this one.")
    if fetch_one("SELECT 1 AS x FROM ussd_code_allocations WHERE code_id = ? AND status = 'active'", (code_id,)):
        raise ValueError("Release this code from its organisations first.")
    execute("UPDATE ussd_codes SET status = 'retired' WHERE id = ?", (code_id,))
    audit("ussd_code.retire", None, ("ussd_code", code_id), {"code": row["code"]}, actor=actor)


def allocations(tenant_id=None, active_only=True):
    where, params = [], []
    if tenant_id is not None:
        where.append("a.tenant_id = ?")
        params.append(tenant_id)
    if active_only:
        where.append("a.status = 'active'")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return fetch_all(
        "SELECT a.id, a.tenant_id, t.name AS tenant_name, a.code_id, c.code, a.code_type, a.org_code, a.setup_fee, a.monthly_fee, "
        "a.status, a.starts_at, a.ends_at FROM ussd_code_allocations a JOIN ussd_codes c ON c.id = a.code_id "
        f"JOIN tenants t ON t.id = a.tenant_id {clause} ORDER BY a.starts_at DESC",
        tuple(params),
    )


def dial_string(allocation):
    """What members dial: *384*88041*2045# for a shared code, the code itself otherwise."""
    if allocation["code_type"] == "shared" and allocation["org_code"]:
        return allocation["code"][:-1] + "*" + allocation["org_code"] + "#"
    return allocation["code"]


def free_org_code(length=4):
    for _ in range(50):
        candidate = f"{secrets.randbelow(9 * 10 ** (length - 1)) + 10 ** (length - 1)}"
        if not fetch_one("SELECT 1 AS x FROM tenants WHERE org_code = ?", (candidate,)):
            return candidate
    return free_org_code(length + 1)


def assign(actor, tenant_id, code_id, org_code=None):
    """Give an organisation a code from the inventory. Returns the new allocation id."""
    row = code(code_id)
    tenant = fetch_one("SELECT id, org_code FROM tenants WHERE id = ?", (tenant_id,))
    if not row or not tenant:
        raise ValueError("No such code or organisation.")
    if row["status"] == "retired":
        raise ValueError(f"{row['code']} is retired.")
    if row["code_type"] in ("extension", "dedicated") and fetch_one(
        "SELECT 1 AS x FROM ussd_code_allocations WHERE code_id = ? AND status = 'active' AND tenant_id <> ?", (code_id, tenant_id)
    ):
        raise ValueError(f"{row['code']} already belongs to another organisation.")
    if row["code_type"] == "shared":
        org_code = str(org_code or tenant["org_code"] or free_org_code()).strip()
        if not ORG_CODE_RE.match(org_code):
            raise ValueError("Org codes are 3-6 digits.")
        clash = fetch_one("SELECT id FROM tenants WHERE org_code = ? AND id <> ?", (org_code, tenant_id))
        if clash:
            raise ValueError(f"Org code {org_code} is used by another organisation.")
        execute("UPDATE tenants SET org_code = ?, updated_at = ? WHERE id = ?", (org_code, int(time.time()), tenant_id))
    else:
        org_code = None
    fee = code_fee(row["code_type"])
    now = int(time.time())
    previous = fetch_all("SELECT id, code_id FROM ussd_code_allocations WHERE tenant_id = ? AND status = 'active'", (tenant_id,))
    for p in previous:
        execute("UPDATE ussd_code_allocations SET status = 'ended', ends_at = ? WHERE id = ?", (now, p["id"]))
        _refresh_status(p["code_id"])
    allocation_id = insert_id(
        "INSERT INTO ussd_code_allocations (tenant_id, code_id, code_type, org_code, setup_fee, monthly_fee, status, starts_at, assigned_by) "
        "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
        (tenant_id, code_id, row["code_type"], org_code, fee["setup_fee"] if fee else 0, fee["monthly_fee"] if fee else 0, now, actor),
    )
    _refresh_status(code_id)
    audit("ussd_code.assign", tenant_id, ("ussd_code", code_id), {
        "code": row["code"], "type": row["code_type"], "org_code": org_code,
        "setup_fee": fee["setup_fee"] if fee else 0, "monthly_fee": fee["monthly_fee"] if fee else 0,
        "replaced": [p["id"] for p in previous],
    }, actor=actor)
    return allocation_id


def release(actor, allocation_id):
    row = fetch_one("SELECT id, tenant_id, code_id FROM ussd_code_allocations WHERE id = ? AND status = 'active'", (allocation_id,))
    if not row:
        raise ValueError("No such active allocation.")
    execute("UPDATE ussd_code_allocations SET status = 'ended', ends_at = ? WHERE id = ?", (int(time.time()), allocation_id))
    _refresh_status(row["code_id"])
    audit("ussd_code.release", row["tenant_id"], ("ussd_code", row["code_id"]), {"allocation": allocation_id}, actor=actor)


def _refresh_status(code_id):
    """available / in_use, from its active allocations (the main code is always in use)."""
    row = code(code_id)
    if not row or row["status"] == "retired":
        return
    used = row["is_main"] or fetch_one("SELECT 1 AS x FROM ussd_code_allocations WHERE code_id = ? AND status = 'active'", (code_id,))
    execute("UPDATE ussd_codes SET status = ? WHERE id = ?", ("in_use" if used else "available", code_id))
