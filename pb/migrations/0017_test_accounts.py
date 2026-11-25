"""(0016 was an early department-contacts table, dropped before release; some local databases have it.)

Test accounts the super admin can turn off and on.

users.disabled: 1 = can't log in anywhere (app, website, portal, USSD); their data is kept.
users.is_test / staff_users.is_test: made by scripts/seed_test_accounts.py, listed on the platform's Test accounts page.
"""


def up(db, m):
    m.add_column("users", "disabled", "INTEGER NOT NULL DEFAULT 0")
    m.add_column("users", "is_test", "INTEGER NOT NULL DEFAULT 0")
    m.add_column("staff_users", "is_test", "INTEGER NOT NULL DEFAULT 0")
