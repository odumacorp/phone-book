"""Create test accounts for every account type and role, in the LOCAL database, and write the list to
docs/test-accounts.md.

    python scripts/seed_test_accounts.py              # local SQLite (phonebook.db, or DB_PATH)
    python scripts/seed_test_accounts.py --use-env-database   # the DATABASE_URL in .env: never production!

Safe to run again: existing accounts are left as they are. No SMS or email is sent.
"""
import os
import sys

USE_ENV_DB = "--use-env-database" in sys.argv
if not USE_ENV_DB:
    os.environ["DATABASE_URL"] = ""  # load_dotenv doesn't override variables that are already set
    os.environ["DATABASE_URL_UNPOOLED"] = ""
os.environ["AT_API_KEY"] = ""  # never send SMS from here
os.environ.setdefault("SECRET_KEY", "local-test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash  # noqa: E402

import app as app_module  # noqa: E402  (runs migrations)
from pb.auth.staff import create_staff  # noqa: E402
from pb.billing import ussd_codes  # noqa: E402
from pb.config import DATABASE_URL, DB_PATH  # noqa: E402
from pb.db import execute, fetch_one  # noqa: E402
from pb.rbac import engine as rbac  # noqa: E402
from pb.tenancy import tenants as tenancy  # noqa: E402
from pb.tenancy.repo import TenantRepo  # noqa: E402
from pb.users import get_user, set_pin  # noqa: E402

STAFF_PASSWORD = "Test-Staff-2026"
USER_PASSWORD = "Test-User-2026"
PIN = "1234"
ACTOR = "seed-script"

STAFF = [
    ("superadmin@test.oduma.local", "Test Super Admin", "platform_super_admin"),
    ("support@test.oduma.local", "Test Support", "platform_support"),
    ("finance@test.oduma.local", "Test Finance", "platform_finance"),
    ("reseller@test.oduma.local", "Test Reseller", "reseller"),
]

# (edition, organisation, plan, org code, roles to create)
ORGS = [
    ("schools", "Test Academy", "enterprise_standard", "2001",
     ["org_owner", "org_admin", "department_supervisor", "teacher", "member", "guardian", "auditor"]),
    ("insurance", "Test Insurance", "enterprise_standard", "2002",
     ["org_owner", "org_admin", "content_publisher", "member", "client", "auditor"]),
    ("legal", "Test Advocates", "enterprise_standard", "2003",
     ["org_owner", "org_admin", "content_publisher", "member", "client", "auditor"]),
    ("sacco", "Test SACCO", "enterprise_standard", "2004",
     ["org_owner", "org_admin", "content_publisher", "member", "auditor"]),
    ("company", "Test Company", "enterprise_pro", "2005",
     ["org_owner", "org_admin", "department_supervisor", "content_publisher", "member", "client", "auditor"]),
]
ROLE_INDEX = {key: i for i, key in enumerate(
    ["org_owner", "org_admin", "department_supervisor", "content_publisher", "teacher", "member", "guardian", "client", "auditor"], 1)}
DEPARTMENT = {"schools": "Form 2", "company": "Sales"}  # supervisors are scoped to this department


def local(phone):
    return "0" + phone[4:]


def make_user(phone, name):
    """A registered account (app password + USSD PIN), as if they had registered in the app."""
    if not get_user(phone, fresh=True):
        set_pin(phone, PIN)
        execute("UPDATE users SET name = ?, password_hash = ? WHERE phone = ?", (name, generate_password_hash(USER_PASSWORD), phone))
    return phone


def make_staff(email, name, role):
    row = fetch_one("SELECT id FROM staff_users WHERE email = ?", (email,))
    if row:
        return row["id"]
    return create_staff("cli", email, name, STAFF_PASSWORD, [role])


def main():
    rows = {"staff": [], "individual": [], "orgs": []}
    with app_module.app.test_request_context():
        staff_ids = {}
        for email, name, role in STAFF:
            staff_ids[role] = make_staff(email, name, role)
            rows["staff"].append((name, email, role))

        phone = make_user("+254700100001", "Test Individual")
        rows["individual"].append(("Test Individual", phone))

        main_code = next(c for c in ussd_codes.codes() if c["is_main"])
        for i, (edition, org_name, plan, org_code, roles) in enumerate(ORGS, 1):
            existing = fetch_one("SELECT id FROM tenants WHERE name = ?", (org_name,))
            if existing:
                tenant_id = existing["id"]
            else:
                tenant_id = tenancy.create_tenant(ACTOR, org_name, edition, plan,
                                                  reseller_id=staff_ids["reseller"] if edition == "company" else None)
                tenancy.update_tenant(rbac.ROOT, tenant_id, status="active")
                ussd_codes.assign(rbac.ROOT, tenant_id, main_code["id"], org_code)
            repo = TenantRepo(tenant_id)
            department = None
            if edition in DEPARTMENT:
                found = repo.one("departments", "name = ?", (DEPARTMENT[edition],))
                department = found["id"] if found else repo.insert("departments", name=DEPARTMENT[edition], created_at=0)
            people = []
            for role in roles:
                phone = f"+2547001{i}{ROLE_INDEX[role]:04d}"
                label = rbac.role_by_key(role)["name"]
                name = f"{org_name.split()[1]} {label.split(' /')[0].split(' (')[0]}"
                make_user(phone, name)
                membership = tenancy.add_member(ACTOR, tenant_id, phone, name)
                scope = ("department", department) if role == "department_supervisor" else ("tenant", None)
                if department and role in ("department_supervisor", "member"):
                    repo.upsert("department_members", ["department_id", "membership_id"], department_id=department,
                                membership_id=membership)
                rbac.grant(rbac.ROOT, tenant_id, rbac.user_principal(phone), role, scope, as_platform=True)
                people.append((label + (f" ({DEPARTMENT[edition]} only)" if role == "department_supervisor" else ""), name, phone))
            rows["orgs"].append((org_name, edition, plan, org_code, tenant_id, people,
                                 "Test Reseller" if edition == "company" else None))
    return rows


def write_list(rows):
    db = DATABASE_URL.split("@")[-1].split("?")[0] if DATABASE_URL else DB_PATH
    code = "*384*88041#"
    out = [
        "# Test accounts",
        "",
        f"Created by `python scripts/seed_test_accounts.py` in **{db}**. Local testing only: never create these in production.",
        "",
        "Start the server locally with `DATABASE_URL= python app.py`, then use http://localhost:8000.",
        "",
        "## Platform staff: http://localhost:8000/platform",
        "",
        f"Password for all: `{STAFF_PASSWORD}`",
        "",
        "| Account | Email | Role | What to test |",
        "|---|---|---|---|",
    ]
    tests = {
        "platform_super_admin": "Everything: organisations, prices, USSD codes, staff, settings (OTP, editions), audit log",
        "platform_support": "Sees organisations, prices (read-only) and the audit log; can't edit prices or open Staff/Settings",
        "platform_finance": "Sees organisations and prices (read-only); can't create organisations",
        "reseller": "Sees only Test Company (theirs); can create organisations; prices read-only",
    }
    for name, email, role in rows["staff"]:
        out.append(f"| {name} | `{email}` | {role} | {tests[role]} |")
    out += [
        "",
        "The `/admin` password (`ADMIN_PASSWORD` in `.env`) also logs in as a break-glass Super Admin.",
        "",
        "## App, website and USSD accounts",
        "",
        f"Every account below: app/website password `{USER_PASSWORD}`, USSD PIN `{PIN}`.",
        "Log in on the website (http://localhost:8000) with the phone number and password, or on USSD",
        f"(`{code}`, e.g. with the Africa's Talking simulator pointed at your ngrok URL) with the phone number and PIN.",
        "",
        "### Individual",
        "",
        "| Account | Phone | What to test |",
        "|---|---|---|",
    ]
    for name, phone in rows["individual"]:
        out.append(f"| {name} | `{local(phone)}` | Personal phone book: contacts, sync, USSD menu, sessions, notifications |")
    out += [
        "",
        "### Organisations",
        "",
        "Each organisation is active and on the shared code with its org code. Until Phase 2 (USSD) and Phase 4 (organisation",
        "portal) these people log in like personal users; their roles and permissions show on the organisation's page in",
        "`/platform` and are what later phases check.",
    ]
    for org_name, edition, plan, org_code, tenant_id, people, reseller in rows["orgs"]:
        out += [
            "",
            f"#### {org_name}: {edition} edition, {plan}, org code `{org_code}` (members will dial `*384*88041*{org_code}#`)"
            + (f", brought in by {reseller}" if reseller else ""),
            f"Platform page: http://localhost:8000/platform/tenants/{tenant_id}",
            "",
            "| Role | Name | Phone |",
            "|---|---|---|",
        ]
        out += [f"| {role} | {name} | `{local(phone)}` |" for role, name, phone in people]
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "test-accounts.md")
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")
    return path


if __name__ == "__main__":
    print(f"Wrote {write_list(main())}")
