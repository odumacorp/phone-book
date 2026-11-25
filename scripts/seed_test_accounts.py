"""Create test accounts and realistic test organisations (schools, SACCOs, companies, law firms) and individuals, and
write the list to docs/test-accounts.md.

    python scripts/seed_test_accounts.py              # local SQLite (phonebook.db, or DB_PATH)
    python scripts/seed_test_accounts.py --use-env-database   # the DATABASE_URL in .env
    python scripts/seed_test_accounts.py --deploy     # on Render (build command): see below

--deploy uses the server's DATABASE_URL and does nothing unless SEED_TEST_ACCOUNTS=true is set on the server. It never
creates the test staff accounts (a Super Admin with password 12345678 would be open to anyone) unless SEED_TEST_STAFF=true,
and never fails the deploy: errors are printed and skipped.

Every test account: password 12345678, USSD PIN 1234 (staff: password 12345678). Accounts are marked as test accounts,
so the super admin can turn them off and on at /platform/test-accounts.

Safe to run again: organisations that already exist keep their data; test accounts get the default password and PIN
back (and their name), but stay turned off if the super admin turned them off. No SMS or email is sent.
"""
import os
import re
import struct
import sys
import zlib

DEPLOY = "--deploy" in sys.argv
USE_ENV_DB = "--use-env-database" in sys.argv or DEPLOY
if DEPLOY and os.getenv("SEED_TEST_ACCOUNTS", "").lower() not in ("1", "true", "yes"):
    print("Test accounts: skipped (set SEED_TEST_ACCOUNTS=true on the server to create them).")
    sys.exit(0)
WITH_STAFF = not DEPLOY or os.getenv("SEED_TEST_STAFF", "").lower() in ("1", "true", "yes")
if not USE_ENV_DB:
    os.environ["DATABASE_URL"] = ""  # load_dotenv doesn't override variables that are already set
    os.environ["DATABASE_URL_UNPOOLED"] = ""
os.environ["AT_API_KEY"] = ""  # never send SMS from here
os.environ.setdefault("SECRET_KEY", "local-test-secret")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from werkzeug.security import check_password_hash, generate_password_hash  # noqa: E402

import app as app_module  # noqa: E402  (runs migrations)
from pb import contacts as personal  # noqa: E402
from pb.auth.staff import create_staff  # noqa: E402
from pb.billing import ussd_codes  # noqa: E402
from pb.config import DATABASE_URL, DB_PATH  # noqa: E402
from pb.db import execute, fetch_one  # noqa: E402
from pb.org import contact_groups, content, structure  # noqa: E402
from pb.rbac import engine as rbac  # noqa: E402
from pb.tenancy import tenants as tenancy  # noqa: E402
from pb.tenancy.repo import TenantRepo  # noqa: E402
from pb.users import get_user, set_pin  # noqa: E402
from pb.org import profile as org_profile  # noqa: E402
from test_data import INDIVIDUALS, ORGANISATIONS, PROFILES  # noqa: E402

PASSWORD = "12345678"
PIN = "1234"
ACTOR = "seed-script"

STAFF = [
    ("superadmin@test.oduma.local", "Test Super Admin", "platform_super_admin"),
    ("support@test.oduma.local", "Test Support", "platform_support"),
    ("finance@test.oduma.local", "Test Finance", "platform_finance"),
    ("reseller@test.oduma.local", "Test Reseller", "reseller"),
]

# The first test organisations: one account per role, generic names. (edition, organisation, plan, org code, roles)
BASIC_ORGS = [
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
EDITION_NAMES = {"schools": "School", "sacco": "SACCO", "company": "Company", "legal": "Law firm", "insurance": "Insurance"}


def local(phone):
    return "0" + phone[4:]


def make_user(phone, name):
    """A registered test account (password + USSD PIN), as if they had registered in the app. Resets the password and
    PIN to the defaults; leaves "turned off" as the super admin set it."""
    if not get_user(phone, fresh=True):
        set_pin(phone, PIN)
    user = get_user(phone, fresh=True)
    if not check_password_hash(user["pin_hash"], PIN):
        execute("UPDATE users SET pin_hash = ?, failed_attempts = 0, locked_until = 0 WHERE phone = ?",
                (generate_password_hash(PIN), phone))
    if not user["password_hash"] or not check_password_hash(user["password_hash"], PASSWORD):
        execute("UPDATE users SET password_hash = ?, pw_failed_attempts = 0, pw_locked_until = 0 WHERE phone = ?",
                (generate_password_hash(PASSWORD), phone))
    execute("UPDATE users SET name = ?, is_test = 1 WHERE phone = ?", (name, phone))
    get_user(phone, fresh=True)
    return phone


def make_staff(email, name, role):
    row = fetch_one("SELECT id FROM staff_users WHERE email = ?", (email,))
    staff_id = row["id"] if row else create_staff("cli", email, name, "temporary-" + PASSWORD, [role])
    # Test staff use the short default password too (create_staff asks for 10+ characters from people)
    execute("UPDATE staff_users SET password_hash = ?, failed_attempts = 0, locked_until = 0, is_test = 1 WHERE id = ?",
            (generate_password_hash(PASSWORD), staff_id))
    return staff_id


def make_org(name, edition, plan, org_code, reseller_id=None):
    """(tenant id, created now?)"""
    existing = fetch_one("SELECT id FROM tenants WHERE name = ?", (name,))
    if existing:
        return existing["id"], False
    tenant_id = tenancy.create_tenant(ACTOR, name, edition, plan, reseller_id=reseller_id)
    tenancy.update_tenant(rbac.ROOT, tenant_id, status="active")
    main_code = next(c for c in ussd_codes.codes() if c["is_main"])
    try:
        ussd_codes.assign(rbac.ROOT, tenant_id, main_code["id"], org_code)
    except ValueError as e:
        print(f"  {name}: no org code ({e})")
    return tenant_id, True


def basic_orgs(staff_ids):
    out = []
    for i, (edition, org_name, plan, org_code, roles) in enumerate(BASIC_ORGS, 1):
        tenant_id, _new = make_org(org_name, edition, plan, org_code, staff_ids["reseller"] if edition == "company" else None)
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
            people.append((label + (f" ({DEPARTMENT[edition]} only)" if role == "department_supervisor" else ""), name, "", phone))
        out.append({"name": org_name, "edition": edition, "plan": plan, "code": org_code, "tenant_id": tenant_id,
                    "people": people, "reseller": "Test Reseller" if edition == "company" else None, "basic": True})
    return out


def badge_png(colour, size=128):
    """A simple round-badge logo (a coloured circle with a white ring) as PNG bytes, without any image library."""
    r, g, b = colour
    rows, c = [], (size - 1) / 2
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            d = ((x - c) ** 2 + (y - c) ** 2) ** 0.5
            if d > c:
                row += bytes([255, 255, 255])
            elif c * 0.62 < d < c * 0.72:
                row += bytes([255, 255, 255])
            elif d < c * 0.3:
                row += bytes([min(255, r + 90), min(255, g + 90), min(255, b + 90)])
            else:
                row += bytes([r, g, b])
        rows.append(bytes(row))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def org_details(o, tenant_id, name):
    """Fill the organisation profile (about, contacts, logo) the first time."""
    if name not in PROFILES:
        return
    about, address, colour = PROFILES[name]
    slug = re.sub(r"[^a-z]", "", name.lower().split(",")[0].split("&")[0])[:20]
    org_profile.save(rbac.ROOT, tenant_id, {"about": about, "address": address, "phone": f"07003{o:02d}000",
                                            "email": f"info@{slug}.example", "website": f"www.{slug}.example"})
    import base64
    org_profile.set_logo(rbac.ROOT, tenant_id, "data:image/png;base64," + base64.b64encode(badge_png(colour)).decode())


def realistic_org(o, spec, phones):
    """Create one organisation from test_data with its departments, groups, people, contacts, notices and contact groups
    (content only when the organisation is new)."""
    tenant_id, new = make_org(spec["name"], spec["edition"], spec["plan"], spec["code"])
    repo, root = TenantRepo(tenant_id), rbac.ROOT
    place = {}  # department / group name -> "d:<id>" / "g:<id>"
    for name, parent in spec["departments"]:
        found = repo.one("departments", "name = ?", (name,))
        place[name] = f"d:{found['id'] if found else structure.save_department(root, tenant_id, name, int(place[parent][2:]) if parent else None)}"
    for name, kind, dept in spec["groups"]:
        found = repo.one("member_groups", "name = ?", (name,))
        place[name] = f"g:{found['id'] if found else structure.save_group(root, tenant_id, name, kind, int(place[dept][2:]) if dept else None)}"

    def audience(names):
        return [place.get(n, n) for n in names]

    people = []
    for p, (name, role, title, where) in enumerate(spec["people"], 1):
        phone = phones.setdefault(name, f"+2547002{o:02d}{p:03d}")  # the same person in two organisations keeps one number
        make_user(phone, name)
        membership = tenancy.add_member(ACTOR, tenant_id, phone, name)
        repo.update("memberships", "id = ?", (membership,), title=title)
        if new:
            structure.set_placements(root, tenant_id, membership, [place[w] for w in where])
        depts = [place[w] for w in where if place[w].startswith("d:")]
        scope = ("department", int(depts[0][2:])) if role == "department_supervisor" and depts else ("tenant", None)
        rbac.grant(root, tenant_id, rbac.user_principal(phone), role, scope, as_platform=True)
        label = rbac.role_by_key(role)["name"].split(" /")[0]
        if role == "department_supervisor" and depts:
            label += f" ({where[0]} only)"
        people.append((label, name, title, phone))
    if new or not org_profile.get(tenant_id)["updated_at"]:
        org_details(o, tenant_id, spec["name"])
    if new:
        ids = {}
        for n, (name, title, group, in_books, pb_aud) in enumerate(spec["contacts"], 1):
            ids[name] = content.save_contact(root, tenant_id, {
                "name": name, "phone": f"07003{o:02d}{n:03d}", "title": title, "group_name": group,
                "in_phone_books": "1" if in_books else "", "pb_audience": audience(pb_aud)}, False)
        for name, phone, title in spec["emergency"]:
            content.save_contact(root, tenant_id, {"name": name, "phone": phone, "title": title, "in_phone_books": "1"}, True)
        for title, body, aud, pinned in spec["notices"]:
            content.save_notice(root, tenant_id, {"title": title, "body": body, "audience": audience(aud),
                                                  "pinned": "1" if pinned else ""})
        for name, members, aud in spec["contact_groups"]:
            gid = contact_groups.save(root, tenant_id, name)
            contact_groups.update(root, tenant_id, gid, [ids[m] for m in members], audience(aud), True)
    return {"name": spec["name"], "edition": spec["edition"], "plan": spec["plan"], "code": spec["code"],
            "tenant_id": tenant_id, "people": people, "reseller": None, "basic": False,
            "departments": [d for d, _ in spec["departments"]], "groups": [g for g, _, _ in spec["groups"]]}


def individuals(phones):
    out = []
    phone = make_user("+254700100001", "Test Individual")
    out.append(("Test Individual", "Personal phone book", phone))
    for i, (name, about, their_contacts) in enumerate(INDIVIDUALS, 1):
        phone = make_user(phones.setdefault(name, f"+254700200{i:03d}"), name)  # also in organisations under this number
        if not fetch_one("SELECT 1 AS x FROM contacts WHERE owner = ?", (phone,)):
            for n, contact in enumerate(their_contacts, 1):
                personal.add_contact(phone, contact, f"+2547004{i:02d}{n:03d}")
        out.append((name, about, phone))
    return out


def main():
    rows = {"staff": [], "orgs": []}
    with app_module.app.test_request_context():
        staff_ids = {"reseller": None}
        for email, name, role in STAFF if WITH_STAFF else []:
            staff_ids[role] = make_staff(email, name, role)
            rows["staff"].append((name, email, role))
        phones = {}
        rows["individuals"] = individuals(phones)
        for o, spec in enumerate(ORGANISATIONS, 1):
            rows["orgs"].append(realistic_org(o, spec, phones))
        rows["orgs"] += basic_orgs(staff_ids)
    return rows


def write_list(rows):
    db = DATABASE_URL.split("@")[-1].split("?")[0] if DATABASE_URL else DB_PATH
    code = "*384*88041#"
    out = [
        "# Test accounts",
        "",
        f"Created by `python scripts/seed_test_accounts.py` in **{db}**. All names and organisations are made up.",
        "",
        f"**Every account: password `{PASSWORD}`, USSD PIN `{PIN}`.** The super admin can turn any of them off and on at",
        "http://localhost:8000/platform/test-accounts (a turned-off account can't log in anywhere).",
        "",
        "Start the server locally with `DATABASE_URL= python app.py`, then use http://localhost:8000.",
        "",
        "## Platform staff: http://localhost:8000/platform",
        "",
        "| Account | Email | Role | What to test |",
        "|---|---|---|---|",
    ]
    tests = {
        "platform_super_admin": "Everything: organisations, prices, USSD codes, staff, settings, test accounts, audit log",
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
        "Log in on the website (http://localhost:8000) with the phone number and password, or on USSD",
        f"(`{code}`) with the phone number and PIN. Owners and admins also log in at http://localhost:8000/platform.",
        "",
        "### Individuals",
        "",
        "| Name | Phone | Who they are |",
        "|---|---|---|",
    ]
    out += [f"| {name} | `{local(phone)}` | {about} |" for name, about, phone in rows["individuals"]]
    for org in rows["orgs"]:
        out += [
            "",
            f"### {org['name']}",
            "",
            f"{EDITION_NAMES.get(org['edition'], org['edition'])} · {org['plan']} · org code `{org['code']}` "
            f"(members dial `*384*88041*{org['code']}#`)" + (f" · brought in by {org['reseller']}" if org["reseller"] else "")
            + f" · http://localhost:8000/platform/tenants/{org['tenant_id']}",
        ]
        if org.get("departments"):
            out += ["", "Departments: " + ", ".join(org["departments"]) + ". Groups: " + ", ".join(org["groups"]) + "."]
        out += ["", "| Role | Name | Job title | Phone |", "|---|---|---|---|"]
        out += [f"| {role} | {name} | {title} | `{local(phone)}` |" for role, name, title, phone in org["people"]]
    path = os.path.join(os.path.dirname(HERE), "docs", "test-accounts.md")
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")
    return path


if __name__ == "__main__":
    if DEPLOY:
        try:
            result = main()
            people = {p[3] for o in result["orgs"] for p in o["people"]} | {p[2] for p in result["individuals"]}
            print(f"Test accounts: {len(result['orgs'])} organisations, {len(people)} people"
                  + (f", {len(result['staff'])} staff" if result["staff"] else " (no staff accounts)") + ". Password 12345678, PIN 1234.")
        except Exception as e:  # never fail the deploy over test data
            print(f"Test accounts: not created ({type(e).__name__}: {e})")
    else:
        print(f"Wrote {write_list(main())}")
