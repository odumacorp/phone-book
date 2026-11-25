"""Operators edit members (details, title, role, departments) and decide who may use USSD from another phone."""
import re

from conftest import app_module, login_ussd
from pb.rbac import engine as rbac
from pb.tenancy import tenants as tenancy
from test_org_content import ADMIN, CLIENT, MEMBER, portal_login, setup_org


def member_id(client, t, phone_local):
    page = client.get(f"/platform/org/{t}/members").get_data(as_text=True)
    row = page[page.index(phone_local) - 600:page.index(phone_local) + 600]
    return re.search(r"\?edit=(\d+)", row[row.index(phone_local):]).group(1)


def test_edit_member_details_title_and_role(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    mid = member_id(client, t, MEMBER)
    edit = client.get(f"/platform/org/{t}/members?edit={mid}").get_data(as_text=True)
    assert "Edit Member" in edit and "Job title" in edit and ">Customer care officer</option>" in edit  # company titles
    client.post(f"/platform/org/{t}/members/{mid}", data={"csrf": csrf, "name": "Wanjiku", "title": "Accountant",
                                                          "member_no": "EMP-7", "role": "content_publisher"})
    page = client.get(f"/platform/org/{t}/members").get_data(as_text=True)
    assert "Wanjiku" in page and "Accountant" in page and "No. EMP-7" in page and "Content Publisher" in page
    with app_module.app.test_request_context():
        assert rbac.role_keys(rbac.user_principal("+254712345678"), t) == {"content_publisher"}


def test_supervisor_needs_department_and_placements_saved(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/departments", data={"csrf": csrf, "name": "Sales"})
    client.post(f"/platform/org/{t}/groups", data={"csrf": csrf, "name": "Field staff"})
    page = client.get(f"/platform/org/{t}/members").get_data(as_text=True)
    sales = re.search(r'value="d:(\d+)">Sales<', page).group(1)
    field = re.search(r'value="g:(\d+)">Field staff<', page).group(1)
    mid = member_id(client, t, MEMBER)
    resp = client.post(f"/platform/org/{t}/members/{mid}", data={"csrf": csrf, "role": "department_supervisor"})
    assert "Choose the department" in client.get(resp.headers["Location"]).get_data(as_text=True)
    client.post(f"/platform/org/{t}/members/{mid}", data={"csrf": csrf, "role": "department_supervisor", "supervises": sales,
                                                          "placements_sent": "1", "placements": [f"d:{sales}", f"g:{field}"]})
    page = client.get(f"/platform/org/{t}/members").get_data(as_text=True)
    assert re.search(r"Department Supervisor</td><td>Sales, Field staff", page)
    with app_module.app.test_request_context():
        assert rbac.can(rbac.user_principal("+254712345678"), "announcements.publish", t, ("department", int(sales)))


def test_role_change_limits(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    me = member_id(client, t, ADMIN)
    resp = client.post(f"/platform/org/{t}/members/{me}", data={"csrf": csrf, "role": "member"})
    assert "can&#39;t change your own role" in client.get(resp.headers["Location"]).get_data(as_text=True)
    mid = member_id(client, t, MEMBER)
    resp = client.post(f"/platform/org/{t}/members/{mid}", data={"csrf": csrf, "role": "org_admin"})
    assert "can&#39;t give that role" in client.get(resp.headers["Location"]).get_data(as_text=True)  # only owners appoint admins
    with app_module.app.test_request_context():
        assert rbac.role_keys(rbac.user_principal("+254712345678"), t) == {"member"}


def test_one_super_admin_hand_over_and_admin_limit(client, register):
    t = setup_org(register)  # enterprise_standard: up to 5 admins; ADMIN is an admin
    register(phone="0719000009")
    with app_module.app.test_request_context():
        tenancy.appoint_owner(rbac.ROOT, t, "0719000009")
    _r, csrf = portal_login(client, "0719000009")
    page = client.get(f"/platform/org/{t}/members").get_data(as_text=True)
    assert "Organisation Super Admin" in page and "1 of 5 allowed" in page
    me = member_id(client, t, "0719000009")
    resp = client.post(f"/platform/org/{t}/members/{me}/remove", data={"csrf": csrf})
    assert "can&#39;t remove yourself" in client.get(resp.headers["Location"]).get_data(as_text=True)
    # hand the role to the admin: they become Super Admin, the previous one becomes an Admin
    aid = member_id(client, t, ADMIN)
    client.post(f"/platform/org/{t}/members/{aid}", data={"csrf": csrf, "role": "org_owner"})
    with app_module.app.test_request_context():
        assert tenancy.super_admin(t) == "user:+254711000001"
        assert rbac.role_keys(rbac.user_principal("+254711000001"), t) == {"org_owner"}
        assert rbac.role_keys(rbac.user_principal("+254719000009"), t) == {"org_admin"}
        assert [a["key"] for a in rbac.tenant_assignments(t)].count("org_owner") == 1
    # the new Super Admin can't be demoted or removed by others; admins can't hand the role over
    _r, csrf2 = portal_login(client, "0719000009")
    resp = client.post(f"/platform/org/{t}/members/{aid}", data={"csrf": csrf2, "role": "member"})
    assert "This is the Super Admin" in client.get(resp.headers["Location"]).get_data(as_text=True)
    resp = client.post(f"/platform/org/{t}/members/{member_id(client, t, MEMBER)}", data={"csrf": csrf2, "role": "org_owner"})
    assert "Only the Super Admin" in client.get(resp.headers["Location"]).get_data(as_text=True)


def test_admin_limit_from_plan(client, register):
    t = setup_org(register)
    with app_module.app.test_request_context():
        tenancy.update_tenant(rbac.ROOT, t, plan_key="enterprise_starter")  # 1 admin allowed, ADMIN already is one
        tenancy.appoint_owner(rbac.ROOT, t, "0713000003")  # the client becomes Super Admin
    _r, csrf = portal_login(client, CLIENT)
    resp = client.post(f"/platform/org/{t}/members/{member_id(client, t, MEMBER)}", data={"csrf": csrf, "role": "org_admin"})
    assert "plan allows 1 admin" in client.get(resp.headers["Location"]).get_data(as_text=True)
    resp = client.post(f"/platform/org/{t}/members", data={"csrf": csrf, "phone": "0714444444", "role": "org_admin"})
    assert "plan allows 1 admin" in client.get(resp.headers["Location"]).get_data(as_text=True)


def test_platform_cannot_strip_the_super_admin(staff_client, register):
    t = setup_org(register)
    with app_module.app.test_request_context():
        tenancy.appoint_owner(rbac.ROOT, t, "0711000001")
        owner = next(a for a in rbac.tenant_assignments(t) if a["key"] == "org_owner")
    client, csrf = staff_client()
    resp = client.post(f"/platform/tenants/{t}/revoke", data={"csrf": csrf, "assignment": owner["id"]})
    assert "needs its Super Admin" in client.get(resp.headers["Location"]).get_data(as_text=True)
    page = client.get(f"/platform/tenants/{t}").get_data(as_text=True)
    assert "Change Super Admin" in page


def test_remove_and_add_back(client, register, dial):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    mid = member_id(client, t, MEMBER)
    client.post(f"/platform/org/{t}/members/{mid}/remove", data={"csrf": csrf})
    page = client.get(f"/platform/org/{t}/members").get_data(as_text=True)
    assert "Removed members" in page
    assert "7. My organisations" not in login_ussd(dial, "+254712345678").last
    client.post(f"/platform/org/{t}/members/{mid}/readd", data={"csrf": csrf})
    assert "7. My organisations" in login_ussd(dial, "+254712345678").last


# ---------------------------------------------------------------- use from another phone: by role, department, title

BORROWED = "+254799000000"


def can_open_from_borrowed_phone(dial, phone_local, full):
    s = login_ussd(dial, BORROWED, phone=phone_local)
    return "only be used from your own phone" not in s.send("7")


def test_other_phone_rules(client, register, dial):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/departments", data={"csrf": csrf, "name": "Field"})
    page = client.get(f"/platform/org/{t}/settings").get_data(as_text=True)
    assert "Only some people" in page and "Job titles" in page
    dept = re.search(r'name="departments" value="(\d+)"', page).group(1)

    client.post(f"/platform/org/{t}/settings", data={"csrf": csrf, "key": "allow_other_phone", "value": "0"})
    assert not can_open_from_borrowed_phone(dial, MEMBER, "+254712345678")

    # by role: clients may, members may not
    client.post(f"/platform/org/{t}/settings", data={"csrf": csrf, "key": "allow_other_phone", "value": "some", "roles": ["client"]})
    assert can_open_from_borrowed_phone(dial, CLIENT, "+254713000003")
    assert not can_open_from_borrowed_phone(dial, MEMBER, "+254712345678")

    # by job title
    mid = member_id(client, t, MEMBER)
    client.post(f"/platform/org/{t}/members/{mid}", data={"csrf": csrf, "role": "member", "title": "Driver"})
    client.post(f"/platform/org/{t}/settings", data={"csrf": csrf, "key": "allow_other_phone", "value": "some",
                                                     "other_titles": "driver, Messenger"})
    assert can_open_from_borrowed_phone(dial, MEMBER, "+254712345678")
    assert not can_open_from_borrowed_phone(dial, CLIENT, "+254713000003")

    # by department
    client.post(f"/platform/org/{t}/members/{mid}", data={"csrf": csrf, "role": "member", "title": "",
                                                          "placements_sent": "1", "placements": [f"d:{dept}"]})
    client.post(f"/platform/org/{t}/settings", data={"csrf": csrf, "key": "allow_other_phone", "value": "some", "departments": [dept]})
    assert can_open_from_borrowed_phone(dial, MEMBER, "+254712345678")

    # own phone always works
    assert "Test Advocates" in login_ussd(dial, "+254713000003", phone=CLIENT).send("7")
    resp = client.post(f"/platform/org/{t}/settings", data={"csrf": csrf, "key": "allow_other_phone", "value": "some"})
    assert "at least one" in client.get(resp.headers["Location"]).get_data(as_text=True)
