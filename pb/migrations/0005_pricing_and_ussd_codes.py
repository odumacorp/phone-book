"""Plans, plan features, volume tiers, add-ons, USSD code fees, and the USSD code inventory and allocations.

Money is integer cents (KES x 100). Every price is a database row the Super Admin edits at /platform/pricing.
The seeded figures are the placeholders from docs/packages-plan.md, to be confirmed.
"""
import time

KES = 100

PLANS = [
    # key, name, audience, model, interval, base price, included members, per-member price, sort
    ("individual_restore", "Individual - Pay per restore", "individual", "per_transaction", None, 50 * KES, None, None, 10),
    ("individual_plus", "Individual - Plus", "individual", "recurring", "month", 99 * KES, None, None, 20),
    ("individual_family", "Individual - Family", "individual", "recurring", "month", 249 * KES, 5, None, 30),
    ("enterprise_starter", "Enterprise Starter", "enterprise", "seat", "month", 2500 * KES, 50, 30 * KES, 40),
    ("enterprise_standard", "Enterprise Standard", "enterprise", "seat", "month", 7500 * KES, 250, 25 * KES, 50),
    ("enterprise_pro", "Enterprise Pro", "enterprise", "seat", "month", 20000 * KES, 1000, 20 * KES, 60),
    ("enterprise_custom", "Enterprise Custom", "enterprise", "quoted", None, None, None, None, 70),
]

# feature key -> value per plan (strings; "1"/"0" for on/off, numbers for limits, "" = unlimited)
FEATURES = {
    "individual_restore": {"restores_included": "1"},
    "individual_plus": {"restores_included": "", "auto_backup": "1", "devices": "2"},
    "individual_family": {"restores_included": "", "auto_backup": "1", "devices": "2", "family_members": "5"},
    "enterprise_starter": {
        "max_admins": "1", "rbac": "0", "departments": "0", "csv_import": "0", "edition_modules": "addon",
        "custom_roles": "0", "custom_ussd_services": "0", "api_webhooks": "0", "analytics": "0", "priority_support": "0",
    },
    "enterprise_standard": {
        "max_admins": "5", "rbac": "1", "departments": "1", "csv_import": "1", "edition_modules": "included",
        "custom_roles": "0", "custom_ussd_services": "0", "api_webhooks": "0", "analytics": "0", "priority_support": "0",
    },
    "enterprise_pro": {
        "max_admins": "", "rbac": "1", "departments": "1", "csv_import": "1", "edition_modules": "included",
        "custom_roles": "1", "custom_ussd_services": "1", "api_webhooks": "1", "analytics": "1", "priority_support": "1",
    },
    "enterprise_custom": {
        "max_admins": "", "rbac": "1", "departments": "1", "csv_import": "1", "edition_modules": "included",
        "custom_roles": "1", "custom_ussd_services": "1", "api_webhooks": "1", "analytics": "1", "priority_support": "1",
        "sla": "1", "white_label": "1",
    },
}

ADDONS = [
    # key, name, audience, price, interval, module, active (prices not set yet: inactive until reviewed)
    ("auto_backup", "Auto-backup subscription", "individual", 0, "month", None, 0),
    ("multi_device", "Multi-device sync", "individual", 0, "month", None, 0),
    ("family", "Family plan add-on", "individual", 0, "month", None, 0),
    ("restore_points", "Contact history / restore points", "individual", 0, "month", None, 0),
    ("school_results", "School results and assignments", "enterprise", 0, "month", "school_results", 0),
    ("sacco_finance", "SACCO financial details", "enterprise", 0, "month", "sacco_finance", 0),
    ("matters", "Law firm matters and advocates", "enterprise", 0, "month", "matters", 0),
    ("client_contacts", "Client contacts", "enterprise", 0, "month", "client_contacts", 0),
]

CODE_FEES = [
    # type, name, setup fee, monthly fee, note
    ("shared", "Shared code (org code)", 0, 0, "Included in the plan"),
    ("extension", "Dedicated extension", 5000 * KES, 3000 * KES, ""),
    ("dedicated", "Dedicated shortcode", 25000 * KES, 15000 * KES, "Plus any carrier charges passed through"),
]

MAIN_CODE = "*384*88041#"  # the current Africa's Talking channel, used for testing; changed at /platform/ussd-codes


def up(db, m):
    now = int(time.time())
    statements = [
        """CREATE TABLE IF NOT EXISTS plans (
               key TEXT PRIMARY KEY,
               name TEXT NOT NULL,
               audience TEXT NOT NULL,
               model TEXT NOT NULL,
               billing_interval TEXT,
               base_price INTEGER,
               included_members INTEGER,
               per_member_price INTEGER,
               annual_discount_pct INTEGER NOT NULL DEFAULT 0,
               trial_days INTEGER NOT NULL DEFAULT 0,
               active INTEGER NOT NULL DEFAULT 1,
               sort INTEGER NOT NULL DEFAULT 0,
               updated_at INTEGER NOT NULL,
               updated_by TEXT
           )""",
        """CREATE TABLE IF NOT EXISTS plan_features (
               plan_key TEXT NOT NULL,
               feature TEXT NOT NULL,
               value TEXT NOT NULL,
               PRIMARY KEY (plan_key, feature)
           )""",
        # Volume tiers for per-member prices: from this many members, each extra member costs this
        f"""CREATE TABLE IF NOT EXISTS plan_tiers (
               id {m.id_col},
               plan_key TEXT NOT NULL,
               from_members INTEGER NOT NULL,
               per_member_price INTEGER NOT NULL
           )""",
        """CREATE TABLE IF NOT EXISTS addons (
               key TEXT PRIMARY KEY,
               name TEXT NOT NULL,
               audience TEXT NOT NULL,
               price INTEGER NOT NULL DEFAULT 0,
               billing_interval TEXT,
               module_key TEXT,
               active INTEGER NOT NULL DEFAULT 0,
               updated_at INTEGER NOT NULL,
               updated_by TEXT
           )""",
        """CREATE TABLE IF NOT EXISTS ussd_code_fees (
               code_type TEXT PRIMARY KEY,
               name TEXT NOT NULL,
               setup_fee INTEGER NOT NULL DEFAULT 0,
               monthly_fee INTEGER NOT NULL DEFAULT 0,
               note TEXT,
               updated_at INTEGER NOT NULL,
               updated_by TEXT
           )""",
        # Codes Oduma Corp has bought, added by the Super Admin; assigned to tenants from this list
        f"""CREATE TABLE IF NOT EXISTS ussd_codes (
               id {m.id_col},
               code TEXT NOT NULL UNIQUE,
               code_type TEXT NOT NULL,
               provider TEXT,
               status TEXT NOT NULL DEFAULT 'available',
               is_main INTEGER NOT NULL DEFAULT 0,
               monthly_cost INTEGER,
               note TEXT,
               added_by TEXT,
               created_at INTEGER NOT NULL
           )""",
        f"""CREATE TABLE IF NOT EXISTS ussd_code_allocations (
               id {m.id_col},
               tenant_id INTEGER NOT NULL,
               code_id INTEGER NOT NULL,
               code_type TEXT NOT NULL,
               org_code TEXT,
               setup_fee INTEGER NOT NULL DEFAULT 0,
               monthly_fee INTEGER NOT NULL DEFAULT 0,
               status TEXT NOT NULL DEFAULT 'active',
               starts_at INTEGER NOT NULL,
               ends_at INTEGER,
               assigned_by TEXT
           )""",
        "CREATE INDEX IF NOT EXISTS idx_code_allocations_tenant ON ussd_code_allocations(tenant_id)",
        "CREATE INDEX IF NOT EXISTS idx_code_allocations_code ON ussd_code_allocations(code_id, status)",
    ]
    for statement in statements:
        db.execute(statement)

    for key, name, audience, model, interval, price, included, per_member, sort in PLANS:
        db.execute(
            m.sql(
                "INSERT INTO plans (key, name, audience, model, billing_interval, base_price, included_members, per_member_price, "
                "sort, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (key) DO NOTHING"
            ),
            (key, name, audience, model, interval, price, included, per_member, sort, now),
        )
        for feature, value in FEATURES.get(key, {}).items():
            db.execute(
                m.sql("INSERT INTO plan_features (plan_key, feature, value) VALUES (?, ?, ?) ON CONFLICT DO NOTHING"),
                (key, feature, value),
            )
    for key, name, audience, price, interval, module, active in ADDONS:
        db.execute(
            m.sql(
                "INSERT INTO addons (key, name, audience, price, billing_interval, module_key, active, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (key) DO NOTHING"
            ),
            (key, name, audience, price, interval, module, active, now),
        )
    for code_type, name, setup, monthly, note in CODE_FEES:
        db.execute(
            m.sql(
                "INSERT INTO ussd_code_fees (code_type, name, setup_fee, monthly_fee, note, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (code_type) DO NOTHING"
            ),
            (code_type, name, setup, monthly, note, now),
        )
    db.execute(
        m.sql(
            "INSERT INTO ussd_codes (code, code_type, provider, status, is_main, note, added_by, created_at) "
            "VALUES (?, 'shared', 'Africa''s Talking', 'in_use', 1, ?, 'migration', ?) ON CONFLICT (code) DO NOTHING"
        ),
        (MAIN_CODE, "Current test channel", now),
    )
