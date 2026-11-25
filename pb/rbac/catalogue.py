"""The permission catalogue and the system roles (named bundles of permissions).

Permissions are the unit every check uses: rbac.can(principal, "results.publish", tenant_id, scope).
System roles are synced into the roles table at startup; tenants can add custom roles from TENANT_PERMISSIONS
when their plan has the custom_roles feature.
"""
import time

PLATFORM_PERMISSIONS = {
    "platform.tenants.view": "View all organisations",
    "platform.tenants.manage": "Create organisations, change their status, modules and settings",
    "platform.tenants.view_own": "View the organisations you brought in (resellers)",
    "platform.tenants.create_own": "Onboard new organisations (resellers)",
    "platform.pricing.view": "View plans and prices",
    "platform.pricing.manage": "Edit plans, prices, add-ons and USSD code fees",
    "platform.flags.manage": "Turn platform features (e.g. OTP) and editions on and off",
    "platform.ussd_codes.manage": "Add purchased USSD codes and assign them to organisations",
    "platform.staff.manage": "Add Oduma Corp staff and give them platform roles",
    "platform.audit.view": "View the audit log",
    "platform.finance.view": "View invoices, payments and reconciliation",
    "platform.finance.manage": "Refunds and payment adjustments",
    "platform.impersonate": "Open an organisation as its admin to fix a support ticket (audited)",
    "platform.support.view": "View support tickets",
    "platform.users.view": "View people's accounts (individuals and organisation members)",
    "platform.users.manage": "Turn accounts off and on, log them out everywhere, unlock them, correct names",
}

TENANT_PERMISSIONS = {
    "tenant.settings.manage": "Organisation settings (USSD from another phone, retention, language)",
    "tenant.billing.manage": "Choose the package and pay invoices",
    "tenant.admins.manage": "Appoint and remove organisation admins",
    "members.view": "View members",
    "members.manage": "Add, import and deactivate members",
    "departments.manage": "Create departments, classes, streams and groups",
    "roles.manage": "Give roles to people (only permissions you hold yourself)",
    "roles.custom": "Create custom roles (if the package allows)",
    "contacts.view": "View the contacts directory (as visibility rules allow)",
    "contacts.manage": "Add and edit directory contacts and contact groups",
    "visibility.manage": "Decide which roles can see which contacts, departments and content",
    "announcements.view": "View announcements",
    "announcements.publish": "Post announcements and emergency broadcasts",
    "emergency.view": "View emergency contacts",
    "emergency.manage": "Add and edit emergency contacts",
    "results.publish": "Post result slips",
    "results.view_own": "View your own result slips",
    "results.view_linked": "View result slips of linked students",
    "assignments.publish": "Post assignments",
    "assignments.view_own": "View assignments for your classes",
    "assignments.view_linked": "View assignments of linked students",
    "guardians.manage": "Link guardians to students",
    "finance.publish": "Post financial details",
    "finance.view_org": "View organisation financial details (rates, products, notices)",
    "finance.view_own": "View your own financial details (shares, savings, loans)",
    "matters.manage": "Manage matters and assign advocates",
    "matters.view_assigned": "View advocates on your matters",
    "ussd_service.create": "Build custom USSD services",
    "ussd_service.publish": "Publish custom USSD services",
    "submissions.view": "View and export form submissions",
    "retention.manage": "Set data retention periods",
    "audit.view": "View the organisation's audit log",
    "data.export": "Export organisation data",
    "support.create": "Raise support tickets",
}

ALL_PERMISSIONS = {**PLATFORM_PERMISSIONS, **TENANT_PERMISSIONS}

_viewer = ["announcements.view", "emergency.view", "contacts.view"]
_member = [*_viewer, "results.view_own", "assignments.view_own", "finance.view_org", "finance.view_own"]

SYSTEM_ROLES = {
    # key: (name, scope, permissions)
    "platform_super_admin": ("Platform Super Admin", "platform", [
        p for p in PLATFORM_PERMISSIONS if p not in ("platform.tenants.view_own", "platform.tenants.create_own")
    ]),
    "platform_support": ("Platform Support", "platform", [
        "platform.tenants.view", "platform.pricing.view", "platform.audit.view", "platform.impersonate", "platform.support.view",
        "platform.users.view",
    ]),
    "platform_finance": ("Platform Finance", "platform", [
        "platform.tenants.view", "platform.pricing.view", "platform.finance.view", "platform.finance.manage",
    ]),
    "reseller": ("Reseller / Sales Agent", "platform", [
        "platform.tenants.view_own", "platform.tenants.create_own", "platform.pricing.view",
    ]),
    "org_owner": ("Organisation Super Admin", "tenant", list(TENANT_PERMISSIONS)),  # exactly one per organisation
    "org_admin": ("Organisation Admin / Operator", "tenant", [
        p for p in TENANT_PERMISSIONS if p not in ("tenant.billing.manage", "tenant.admins.manage")
    ]),
    "department_supervisor": ("Department Supervisor", "tenant", [
        "members.view", "members.manage", "roles.manage", "contacts.view", "contacts.manage", "visibility.manage",
        "announcements.publish", "emergency.manage", "support.create",
        *_member,  # supervisors are members too, and can give people the Member role in their departments
    ]),
    "content_publisher": ("Content Publisher", "tenant", [
        "members.view", "announcements.publish", "results.publish", "assignments.publish", "finance.publish", *_viewer,
    ]),
    "teacher": ("Teacher", "tenant", [
        "members.view", "announcements.publish", "results.publish", "assignments.publish", *_viewer,
    ]),
    "member": ("Member", "tenant", _member),
    "guardian": ("Guardian", "tenant", [*_viewer, "results.view_linked", "assignments.view_linked"]),
    "client": ("Client", "tenant", [*_viewer, "matters.view_assigned"]),
    "auditor": ("Auditor (read-only)", "tenant", ["audit.view", "members.view", *_viewer, "finance.view_org"]),
}


def sync_system_roles(db, sql):
    """Make the roles table match SYSTEM_ROLES (called at startup, inside init_db's transaction)."""
    now = int(time.time())
    for key, (name, scope, permissions) in SYSTEM_ROLES.items():
        unknown = set(permissions) - set(ALL_PERMISSIONS)
        assert not unknown, f"{key}: unknown permissions {unknown}"
        row = db.execute(sql("SELECT id FROM roles WHERE tenant_id IS NULL AND key = ?"), (key,)).fetchone()
        if row:
            role_id = row["id"]
            db.execute(sql("UPDATE roles SET name = ?, scope = ?, is_system = 1 WHERE id = ?"), (name, scope, role_id))
        else:
            db.execute(
                sql("INSERT INTO roles (tenant_id, key, name, scope, is_system, created_by, created_at) VALUES (NULL, ?, ?, ?, 1, 'system', ?)"),
                (key, name, scope, now),
            )
            role_id = db.execute(sql("SELECT id FROM roles WHERE tenant_id IS NULL AND key = ?"), (key,)).fetchone()["id"]
        db.execute(sql("DELETE FROM role_permissions WHERE role_id = ?"), (role_id,))
        for permission in sorted(set(permissions)):
            db.execute(sql("INSERT INTO role_permissions (role_id, permission) VALUES (?, ?)"), (role_id, permission))
