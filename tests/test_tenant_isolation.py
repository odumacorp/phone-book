"""One organisation can't read or change another's data."""
import os
import re

import pytest

from pb.rbac import engine as rbac
from pb.tenancy import tenants as tenancy
from pb.tenancy.repo import TENANT_TABLES, TenantRepo

ROOT = rbac.ROOT
PB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pb")

# Files allowed to query tenant tables without TenantRepo, and why
ALLOWED_RAW = {
    "tenancy/repo.py": "the repository itself",
    "rbac/engine.py": "role tables (platform roles have no tenant); every query filters tenant_id explicitly",
    "tenancy/tenants.py": "platform-wide lists (all organisations with member counts) and a person's own memberships",
    "billing/ussd_codes.py": "the platform code inventory spans organisations",
    "auth/otp.py": "a person's own memberships (does any of their organisations force OTP?)",
    "org/reminders.py": "the scheduled-reminder job finds due reminders across organisations",
}


def two_tenants():
    a = tenancy.create_tenant(ROOT, "Alpha SACCO", "sacco", "enterprise_standard")
    b = tenancy.create_tenant(ROOT, "Beta SACCO", "sacco", "enterprise_standard")
    return a, b


def test_repo_only_sees_its_tenant(ctx):
    a, b = two_tenants()
    tenancy.add_member(ROOT, a, "0711000001", "Alice")
    tenancy.add_member(ROOT, b, "0722000002", "Bob")
    ra, rb = TenantRepo(a), TenantRepo(b)
    assert [m["display_name"] for m in ra.all("memberships")] == ["Alice"]
    assert [m["display_name"] for m in rb.all("memberships")] == ["Bob"]
    bob = rb.one("memberships")
    assert ra.one("memberships", "id = ?", (bob["id"],)) is None  # knowing the id isn't enough
    assert ra.update("memberships", "id = ?", (bob["id"],), display_name="Hacked") == 0
    assert ra.delete("memberships", "id = ?", (bob["id"],)) == 0
    assert rb.one("memberships", "id = ?", (bob["id"],))["display_name"] == "Bob"


def test_repo_refuses_bad_input(ctx):
    with pytest.raises(ValueError):
        TenantRepo(0)
    with pytest.raises(ValueError):
        TenantRepo("1")
    repo = TenantRepo(1)
    with pytest.raises(ValueError):
        repo.all("users")  # not a tenant table
    with pytest.raises(ValueError):
        repo.insert("memberships", tenant_id=2, user_phone="+254700000000", joined_at=0)  # can't pick another tenant
    with pytest.raises(ValueError):
        repo.update("memberships", "", (), status="x")  # no blanket updates
    with pytest.raises(ValueError):
        repo.insert("memberships", **{"user_phone; DROP TABLE x": "1"})


def test_modules_and_settings_are_per_tenant(ctx):
    a, b = two_tenants()
    tenancy.set_module(ROOT, a, "sacco_finance", False)
    tenancy.set_setting(ROOT, a, "allow_other_phone", "0")
    assert not tenancy.module_enabled(a, "sacco_finance")
    assert tenancy.module_enabled(b, "sacco_finance")
    assert not tenancy.allow_other_phone(a)
    assert tenancy.allow_other_phone(b)  # default: allowed


def test_permissions_are_per_tenant(ctx):
    a, b = two_tenants()
    tenancy.appoint_owner(ROOT, a, "0711000001")
    owner = rbac.user_principal("+254711000001")
    assert rbac.can(owner, "finance.publish", a)
    assert not rbac.can(owner, "finance.publish", b)
    assert rbac.permissions(owner, b) == set()


def test_no_raw_queries_on_tenant_tables_outside_allowlist():
    pattern = re.compile(r"\b(?:FROM|INTO|UPDATE|JOIN)\s+(" + "|".join(sorted(TENANT_TABLES)) + r")\b", re.IGNORECASE)
    offenders = []
    for root, _dirs, files in os.walk(PB):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, PB)
            if rel in ALLOWED_RAW or rel.startswith("migrations" + os.sep):
                continue
            for n, line in enumerate(open(path), 1):
                if pattern.search(line):
                    offenders.append(f"pb/{rel}:{n}: {line.strip()}")
    assert not offenders, "Use TenantRepo for tenant tables:\n" + "\n".join(offenders)
