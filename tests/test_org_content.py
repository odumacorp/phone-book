"""Organisation admins publish notices and contacts in /platform; members see them on USSD and in the app."""
import re

from conftest import app_module, auth, csrf_of, login_ussd
from pb.rbac import engine as rbac
from pb.tenancy import tenants as tenancy

ADMIN, MEMBER, CLIENT, OUTSIDER = "0711000001", "0712345678", "0713000003", "0714000004"


def setup_org(register, edition="company", name="Test Advocates"):
    """An organisation with an admin, a member and a client, all registered. Returns tenant id."""
    for phone in (ADMIN, MEMBER, CLIENT, OUTSIDER):
        register(phone=phone)
    with app_module.app.test_request_context():
        t = tenancy.create_tenant(rbac.ROOT, name, edition, "enterprise_standard")
        tenancy.update_tenant(rbac.ROOT, t, status="active")
        for phone, role in ((ADMIN, "org_admin"), (MEMBER, "member"), (CLIENT, "client")):
            tenancy.add_member(rbac.ROOT, t, phone, role.title())
            rbac.grant(rbac.ROOT, t, rbac.user_principal("+254" + phone[1:]), role, as_platform=True)
    return t


def portal_login(client, login, password="secret-pass-1"):
    token = csrf_of(client)
    resp = client.post("/platform/login", data={"csrf": token, "login": login, "password": password})
    return resp, csrf_of(client, "/platform/org") if resp.headers.get("Location", "").endswith("/platform/") else None


def test_org_admin_logs_in_with_phone_and_lands_in_workspace(client, register):
    t = setup_org(register)
    resp, _ = portal_login(client, ADMIN)
    assert resp.status_code == 302
    page = client.get("/platform/", follow_redirects=True).get_data(as_text=True)
    assert "Test Advocates" in page and "Post a notice" in page
    assert client.get("/platform/pricing").status_code == 403  # no platform pages
    assert client.get("/platform/tenants").status_code == 403
    other = None
    with app_module.app.test_request_context():
        other = tenancy.create_tenant(rbac.ROOT, "Other firm", "legal", "enterprise_standard")
    assert client.get(f"/platform/org/{other}").status_code == 404  # not their organisation
    assert client.get(f"/platform/org/{t}/notices").status_code == 200


def test_member_without_admin_role_has_nothing_to_manage(client, register):
    t = setup_org(register)
    portal_login(client, MEMBER)
    page = client.get("/platform/", follow_redirects=True).get_data(as_text=True)
    assert "Nothing to manage yet" in page
    assert client.get(f"/platform/org/{t}").status_code == 404


def test_wrong_password_on_portal(client, register):
    setup_org(register)
    resp, _ = portal_login(client, ADMIN, "wrong-password")
    assert "error=" in resp.headers["Location"]


def test_publish_and_members_see_it_on_ussd(client, register, dial, fake_sms):
    t = setup_org(register)
    _resp, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/notices", data={"csrf": csrf, "title": "Office closed Friday", "body": "We reopen Monday at 8am. " * 12})
    client.post(f"/platform/org/{t}/notices", data={"csrf": csrf, "title": "Client fees update", "body": "New rates.", "audience": ["client"]})
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Front desk", "phone": "0720111222", "group_name": "Office"})
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Advocate Wanjiku", "phone": "0720333444",
                                                      "title": "Advocate", "group_name": "Advocates"})
    client.post(f"/platform/org/{t}/emergency", data={"csrf": csrf, "name": "Ambulance", "phone": "1199"})

    s = login_ussd(dial, "+254712345678")  # the member
    assert "7. My organisations" in s.last
    org_menu = s.send("7")  # only one organisation: straight in
    assert "Test Advocates" in org_menu and "1. Notices (1)" in org_menu  # the client-only notice is hidden
    assert "Office closed Friday" in s.send("1")
    first = s.send("1")
    assert "98. Next (1/" in first and "1. SMS me this" in first
    assert "We reopen" in s.send("98")
    assert "Sent to you by SMS" in s.send("1")
    assert any("Test Advocates: Office closed Friday" in m for m in fake_sms.to("+254712345678"))

    s = login_ussd(dial, "+254712345678")
    s.send("7")
    groups = s.send("2")
    assert "1. Advocates" in groups and "2. Office" in groups
    assert "Advocate Wan" in s.send("1")  # names are shortened on list screens
    assert "1. Call\n2. Send SMS\n3. Copy number" in s.send("1")

    s = login_ussd(dial, "+254712345678")
    s.send("7")
    assert "Ambulance" in s.send("3")
    assert "1. Call\n3. Copy number" in s.send("1")  # a short code: dial it yourself, no SMS


def test_client_sees_client_notice(client, register, dial):
    t = setup_org(register)
    _resp, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/notices", data={"csrf": csrf, "title": "Client fees update", "body": "New rates.", "audience": ["client"]})
    s = login_ussd(dial, "+254713000003", phone=CLIENT)
    s.send("7")
    assert "Client fees update" in s.send("1")


def test_outsider_sees_nothing(register, dial):
    setup_org(register)
    s = login_ussd(dial, "+254714000004", phone=OUTSIDER)
    assert "7. My organisations" not in s.last and "Or type a name" in s.last
    assert "not in any organisation" in s.send("7")


def test_own_phone_only_setting(client, register, dial):
    t = setup_org(register)
    with app_module.app.test_request_context():
        tenancy.set_setting(rbac.ROOT, t, "allow_other_phone", "0")
    s = login_ussd(dial, "+254799000000")  # member logging in from a borrowed phone
    assert "only be used from your own phone" in s.send("7")
    s = login_ussd(dial, "+254712345678")
    assert "Test Advocates" in s.send("7")


def test_app_api_shows_visible_content(client, register):
    t = setup_org(register)
    _resp, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/notices", data={"csrf": csrf, "title": "For everyone", "body": "Hi", "is_emergency": "1"})
    client.post(f"/platform/org/{t}/notices", data={"csrf": csrf, "title": "Clients only", "body": "x", "audience": ["client"]})
    client.post(f"/platform/org/{t}/emergency", data={"csrf": csrf, "name": "Security", "phone": "0700999888"})
    token = client.post("/api/login", json={"login": MEMBER, "password": "secret-pass-1"}).get_json()["token"]
    orgs = client.get("/api/orgs", headers=auth(token)).get_json()["orgs"]
    assert orgs == [{"id": t, "name": "Test Advocates", "notices": 1, "urgent": 1}]
    detail = client.get(f"/api/orgs/{t}", headers=auth(token)).get_json()
    assert [n["title"] for n in detail["notices"]] == ["For everyone"]
    assert detail["emergency"][0]["name"] == "Security"
    outsider = client.post("/api/login", json={"login": OUTSIDER, "password": "secret-pass-1"}).get_json()["token"]
    assert client.get(f"/api/orgs/{t}", headers=auth(outsider)).status_code == 404


def test_edit_delete_and_validation(client, register):
    t = setup_org(register)
    _resp, csrf = portal_login(client, ADMIN)
    resp = client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Bad", "phone": "abc"})
    assert "valid phone" in client.get(resp.headers["Location"]).get_data(as_text=True)
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Desk", "phone": "0720111222"})
    page = client.get(f"/platform/org/{t}/contacts").get_data(as_text=True)
    contact_id = re.search(r"\?edit=(\d+)", page).group(1)
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "contact_id": contact_id, "name": "Reception", "phone": "0720111222"})
    assert "Reception" in client.get(f"/platform/org/{t}/contacts").get_data(as_text=True)
    client.post(f"/platform/org/{t}/contacts/{contact_id}/delete", data={"csrf": csrf})
    assert "No contacts yet" in client.get(f"/platform/org/{t}/contacts").get_data(as_text=True)
    assert "org_contact.delete" in str(_audit(t))


def test_admin_adds_and_removes_members(client, register, dial):
    t = setup_org(register)
    register(phone="0715000005")
    _resp, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/members", data={"csrf": csrf, "phone": "0715000005", "name": "New person", "role": "member"})
    assert "New person" in client.get(f"/platform/org/{t}/members").get_data(as_text=True)
    s = login_ussd(dial, "+254715000005", phone="0715000005")
    assert "7. My organisations" in s.last
    resp = client.post(f"/platform/org/{t}/members", data={"csrf": csrf, "phone": "0715000006", "role": "org_owner"})
    assert resp.status_code == 302  # admins can't create owners: refused with a message, not a crash
    page = client.get(f"/platform/org/{t}/members").get_data(as_text=True)
    membership = re.findall(r"/members/(\d+)/remove", page)
    for m in membership:
        client.post(f"/platform/org/{t}/members/{m}/remove", data={"csrf": csrf})
    remaining = client.get(f"/platform/org/{t}/members").get_data(as_text=True)
    assert "Admin" in remaining  # couldn't remove themselves


def test_staff_can_open_workspace_from_tenant_page(staff_client, register):
    t = setup_org(register)
    client, csrf = staff_client()
    assert "Open workspace" in client.get(f"/platform/tenants/{t}").get_data(as_text=True)
    assert client.get(f"/platform/org/{t}").status_code == 200
    client.post(f"/platform/org/{t}/notices", data={"csrf": csrf, "title": "From support", "body": "Set up for you"})
    assert "From support" in client.get(f"/platform/org/{t}/notices").get_data(as_text=True)


def test_modules_decide_what_shows(client, register, dial):
    t = setup_org(register, edition="legal")  # law firms: no emergency contacts module
    _resp, csrf = portal_login(client, ADMIN)
    assert client.get(f"/platform/org/{t}/emergency").status_code == 403
    assert client.post(f"/platform/org/{t}/emergency", data={"csrf": csrf, "name": "X", "phone": "999"}).status_code == 403
    s = login_ussd(dial, "+254712345678")
    menu = s.send("7")
    assert "Notices" in menu and "Contacts" in menu and "Emergency" not in menu


def test_suspended_org_shows_nothing(client, register, dial):
    t = setup_org(register)
    _resp, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/notices", data={"csrf": csrf, "title": "Hello", "body": "x"})
    with app_module.app.test_request_context():
        tenancy.update_tenant(rbac.ROOT, t, status="suspended")
    s = login_ussd(dial, "+254712345678")
    assert "7. My organisations" not in s.last


def _audit(tenant_id):
    from pb.audit import audit_entries
    with app_module.app.app_context():
        return [r["action"] for r in audit_entries(tenant_id=tenant_id)]
