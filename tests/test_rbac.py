"""RBAC rules: scopes, granting only what you hold, custom roles, platform roles, suspended organisations."""
import pytest

from pb.rbac import engine as rbac
from pb.rbac.catalogue import SYSTEM_ROLES
from pb.tenancy import tenants as tenancy
from pb.tenancy.repo import TenantRepo

ROOT = rbac.ROOT


def new_tenant(name="Acme School", edition="schools", plan="enterprise_standard"):
    return tenancy.create_tenant(ROOT, name, edition, plan)


def user(phone):
    return rbac.user_principal(phone)


def test_system_roles_synced(ctx):
    for key in SYSTEM_ROLES:
        assert rbac.role_by_key(key), key
    assert "results.publish" in rbac.role_permissions(rbac.role_by_key("teacher")["id"])
    assert "tenant.billing.manage" not in rbac.role_permissions(rbac.role_by_key("org_admin")["id"])


def test_owner_has_everything_member_little(ctx):
    t = new_tenant()
    tenancy.appoint_owner(ROOT, t, "0711000001")
    owner = user("+254711000001")
    assert rbac.can(owner, "tenant.billing.manage", t)
    assert rbac.can(owner, "results.publish", t, ("group", 999))  # tenant scope covers anything in the tenant
    tenancy.add_member(ROOT, t, "0711000002")
    rbac.grant(owner, t, user("+254711000002"), "member")
    assert rbac.can(user("+254711000002"), "announcements.view", t)
    assert not rbac.can(user("+254711000002"), "announcements.publish", t)


def test_roles_do_not_leak_across_tenants(ctx):
    a, b = new_tenant("A"), new_tenant("B")
    tenancy.appoint_owner(ROOT, a, "0711000001")
    owner = user("+254711000001")
    assert rbac.can(owner, "members.manage", a)
    assert not rbac.can(owner, "members.manage", b)
    with pytest.raises(rbac.PermissionDenied):
        rbac.grant(owner, b, user("+254711000009"), "member")


def test_department_scope_and_sub_departments(ctx):
    t = new_tenant("Corp", "company")
    repo = TenantRepo(t)
    sales = repo.insert("departments", name="Sales", created_at=0)
    nairobi = repo.insert("departments", name="Sales Nairobi", parent_id=sales, created_at=0)
    finance = repo.insert("departments", name="Finance", created_at=0)
    group = repo.insert("member_groups", name="Key accounts", kind="custom", department_id=nairobi, created_at=0)
    m = tenancy.add_member(ROOT, t, "0711000003")
    repo.insert("department_members", returning_id=False, department_id=nairobi, membership_id=m)

    tenancy.appoint_owner(ROOT, t, "0711000001")
    owner = user("+254711000001")
    sup = user("+254711000002")
    tenancy.add_member(ROOT, t, "0711000002")
    rbac.grant(owner, t, sup, "department_supervisor", ("department", sales))

    assert rbac.can(sup, "announcements.publish", t, ("department", sales))
    assert rbac.can(sup, "announcements.publish", t, ("department", nairobi))  # sub-department
    assert rbac.can(sup, "announcements.publish", t, ("group", group))  # group in a sub-department
    assert rbac.can(sup, "members.manage", t, ("member", m))  # member of a sub-department
    assert not rbac.can(sup, "announcements.publish", t, ("department", finance))
    assert not rbac.can(sup, "announcements.publish", t)  # not tenant-wide
    assert rbac.can_anywhere(sup, "announcements.publish", t)


def test_supervisor_gives_member_role_in_own_department_only(ctx):
    t = new_tenant("Corp", "company")
    sales = TenantRepo(t).insert("departments", name="Sales", created_at=0)
    finance = TenantRepo(t).insert("departments", name="Finance", created_at=0)
    tenancy.appoint_owner(ROOT, t, "0711000001")
    owner, sup = user("+254711000001"), user("+254711000002")
    rbac.grant(owner, t, sup, "department_supervisor", ("department", sales))
    rbac.grant(sup, t, user("+254711000005"), "member", ("department", sales))
    assert rbac.can(user("+254711000005"), "announcements.view", t, ("department", sales))
    with pytest.raises(rbac.PermissionDenied):
        rbac.grant(sup, t, user("+254711000005"), "member", ("department", finance))


def test_supervisor_cannot_grant_permissions_they_lack(ctx):
    t = new_tenant("Corp", "company")
    sales = TenantRepo(t).insert("departments", name="Sales", created_at=0)
    finance = TenantRepo(t).insert("departments", name="Finance", created_at=0)
    tenancy.appoint_owner(ROOT, t, "0711000001")
    owner, sup = user("+254711000001"), user("+254711000002")
    rbac.grant(owner, t, sup, "department_supervisor", ("department", sales))
    with pytest.raises(rbac.PermissionDenied):
        rbac.grant(sup, t, user("+254711000006"), "teacher", ("department", sales))  # results.publish: not held
    with pytest.raises(rbac.PermissionDenied):
        rbac.grant(sup, t, user("+254711000006"), "department_supervisor", ("department", finance))  # outside their scope
    with pytest.raises(rbac.PermissionDenied):
        rbac.grant(sup, t, user("+254711000006"), "org_admin")  # admins only by owners


def test_scope_must_belong_to_tenant(ctx):
    a, b = new_tenant("A", "company"), new_tenant("B", "company")
    other_dept = TenantRepo(b).insert("departments", name="X", created_at=0)
    tenancy.appoint_owner(ROOT, a, "0711000001")
    with pytest.raises(ValueError):
        rbac.grant(user("+254711000001"), a, user("+254711000002"), "member", ("department", other_dept))


def test_revoke(ctx):
    t = new_tenant()
    tenancy.appoint_owner(ROOT, t, "0711000001")
    owner = user("+254711000001")
    assignment = rbac.grant(owner, t, user("+254711000002"), "teacher")
    assert rbac.can(user("+254711000002"), "assignments.publish", t)
    rbac.revoke(owner, t, assignment)
    assert not rbac.can(user("+254711000002"), "assignments.publish", t)


def test_custom_roles_need_plan_and_held_permissions(ctx):
    t = new_tenant(plan="enterprise_standard")
    tenancy.appoint_owner(ROOT, t, "0711000001")
    owner = user("+254711000001")
    assert not tenancy.plan_allows(t, "custom_roles")
    with pytest.raises(rbac.PermissionDenied):
        rbac.create_custom_role(owner, t, "bursar", "Bursar", ["finance.publish"], tenancy.plan_allows(t, "custom_roles"))
    tenancy.update_tenant(ROOT, t, plan_key="enterprise_pro")
    role_id = rbac.create_custom_role(owner, t, "bursar", "Bursar", ["finance.publish", "members.view"], tenancy.plan_allows(t, "custom_roles"))
    assert rbac.role_permissions(role_id) == {"finance.publish", "members.view"}
    rbac.grant(owner, t, user("+254711000007"), "bursar")
    assert rbac.can(user("+254711000007"), "finance.publish", t)
    with pytest.raises(ValueError):
        rbac.create_custom_role(owner, t, "x", "X", ["platform.pricing.manage"], True)  # not a tenant permission
    other = new_tenant("Other", plan="enterprise_pro")
    assert rbac.role_by_key("bursar", other) is None  # custom roles are per tenant


def test_suspended_tenant_grants_nothing(ctx):
    t = new_tenant()
    tenancy.appoint_owner(ROOT, t, "0711000001")
    assert rbac.can(user("+254711000001"), "members.view", t)
    tenancy.update_tenant(ROOT, t, status="suspended")
    assert not rbac.can(user("+254711000001"), "members.view", t)
    assert not tenancy.module_enabled(t, "members")


def test_platform_roles(ctx, make_staff):
    sid = make_staff(email="support@oduma.co.ke", roles=["platform_support"])
    support = rbac.staff_principal(sid)
    assert rbac.can(support, "platform.tenants.view")
    assert not rbac.can(support, "platform.pricing.manage")
    with pytest.raises(rbac.PermissionDenied):
        rbac.grant_platform(support, sid, "platform_super_admin")
    assert rbac.can(ROOT, "platform.pricing.manage")
    assert not rbac.can(ROOT, "members.view", 1)  # platform staff get no tenant permissions by default


def test_unknown_permission_is_an_error(ctx):
    with pytest.raises(ValueError):
        rbac.can(ROOT, "nonsense.permission")


def test_permission_changes_are_audited(ctx):
    from pb.audit import audit_entries
    t = new_tenant()
    tenancy.appoint_owner(ROOT, t, "0711000001")
    actions = [r["action"] for r in audit_entries(tenant_id=t)]
    assert "role.grant" in actions and "tenant.create" in actions and "member.add" in actions
