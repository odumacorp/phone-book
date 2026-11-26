"""Only roles that fit the kind of organisation are offered; admins add their own roles and job titles."""
import re

from conftest import app_module
from pb.rbac import engine as rbac
from pb.tenancy import tenants as tenancy
from test_members_and_phone_rules import member_id
from test_org_content import ADMIN, MEMBER, portal_login, setup_org


def role_options(page):
    select = page[page.index('<select name="role">'):]
    return re.findall(r'<option value="([a-z_]+)"', select[:select.index("</select>")])


def test_roles_fit_the_kind_of_organisation(client, register):
    t = setup_org(register)  # company: no guardians or teachers
    portal_login(client, ADMIN)
    options = role_options(client.get(f"/platform/org/{t}/members").get_data(as_text=True))
    assert "client" in options and "member" in options
    assert "guardian" not in options and "teacher" not in options
    with app_module.app.test_request_context():
        school = tenancy.create_tenant(rbac.ROOT, "Hill School", "schools", "enterprise_standard")
        assert {"guardian", "teacher"} <= dict(tenancy.available_roles(school)).keys()
        assert "client" not in dict(tenancy.available_roles(school))


def test_roles_not_offered_are_refused(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    resp = client.post(f"/platform/org/{t}/members/{member_id(client, t, MEMBER)}", data={"csrf": csrf, "role": "guardian"})
    assert "isn&#39;t used in this organisation" in client.get(resp.headers["Location"]).get_data(as_text=True)
    resp = client.post(f"/platform/org/{t}/members", data={"csrf": csrf, "phone": "0715555555", "role": "teacher"})
    assert "isn&#39;t used in this organisation" in client.get(resp.headers["Location"]).get_data(as_text=True)


def test_admin_adds_a_role_that_works_like_another(client, register):
    t = setup_org(register)  # enterprise_standard: no custom permissions, but named roles are fine
    _r, csrf = portal_login(client, ADMIN)
    page = client.get(f"/platform/org/{t}/lists").get_data(as_text=True)
    assert "Roles in Test Advocates" in page and "Choosing exactly what a role can do is part of the Pro plan" in page
    client.post(f"/platform/org/{t}/lists/roles", data={"csrf": csrf, "name": "Field agent", "like": "content_publisher"})
    page = client.get(f"/platform/org/{t}/members").get_data(as_text=True)
    assert "Field agent" in page
    custom = next(k for k in role_options(page) if k.startswith("custom_"))
    client.post(f"/platform/org/{t}/members/{member_id(client, t, MEMBER)}", data={"csrf": csrf, "role": custom})
    with app_module.app.test_request_context():
        assert rbac.can(rbac.user_principal("+254712345678"), "announcements.publish", t)
    # can't remove a role people still have; can't change permissions without Pro
    role_id = re.search(r"/lists/roles/(\d+)/delete", client.get(f"/platform/org/{t}/lists").get_data(as_text=True)).group(1)
    resp = client.post(f"/platform/org/{t}/lists/roles/{role_id}/delete", data={"csrf": csrf})
    assert "still have the Field agent role" in client.get(resp.headers["Location"]).get_data(as_text=True)
    resp = client.post(f"/platform/org/{t}/lists/roles", data={"csrf": csrf, "name": "Viewer", "like": "member", "customise": "1",
                                                                 "permissions": ["announcements.view"]})
    assert "part of the Pro plan" in client.get(resp.headers["Location"]).get_data(as_text=True)
    resp = client.post(f"/platform/org/{t}/lists/roles", data={"csrf": csrf, "name": "client", "like": "member"})
    assert "already a role called" in client.get(resp.headers["Location"]).get_data(as_text=True)


def test_custom_permissions_on_pro(client, register):
    t = setup_org(register)
    with app_module.app.test_request_context():
        tenancy.update_tenant(rbac.ROOT, t, plan_key="enterprise_pro")
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/lists/roles", data={"csrf": csrf, "name": "Viewer", "like": "member", "customise": "1",
                                                         "permissions": ["announcements.view", "contacts.view"]})
    with app_module.app.test_request_context():
        role = rbac.role_by_key("custom_viewer", t)
        assert rbac.role_permissions(role["id"]) == {"announcements.view", "contacts.view"}


def test_own_titles_listed_first(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/lists/titles", data={"csrf": csrf, "add": "Claims officer, Underwriter"})
    page = client.get(f"/platform/org/{t}/members").get_data(as_text=True)
    own = page[page.index('optgroup label="Your titles"'):]
    assert own.index("Claims officer") < own.index('optgroup label="Suggested for you"')
    client.post(f"/platform/org/{t}/lists/titles", data={"csrf": csrf, "remove": "Underwriter"})
    with app_module.app.test_request_context():
        assert tenancy.org_titles(t) == ["Claims officer"]


def test_members_cannot_open_roles_page(client, register):
    t = setup_org(register)
    portal_login(client, MEMBER)
    assert client.get(f"/platform/org/{t}/lists").status_code == 404


def test_click_a_role_to_see_who_has_it(client, register):
    from test_org_content import ADMIN, portal_login, setup_org
    t = setup_org(register)
    _r, _csrf = portal_login(client, ADMIN)
    page = client.get(f"/platform/org/{t}/lists").get_data(as_text=True)
    assert f'href="/platform/org/{t}/roles/client"' in page and f'href="/platform/org/{t}/roles/owner"' in page
    role = client.get(f"/platform/org/{t}/roles/client").get_data(as_text=True)
    assert "<h1>Client</h1>" in role and "0713000003" in role and "/members?edit=" in role  # the client, with Manage
    assert f'href="/platform/org/{t}/roles/member"' in role and f'href="/platform/org/{t}/roles/org_admin"' in role  # tabs
    assert 'class="rtab on"' in role
    owner = client.get(f"/platform/org/{t}/roles/owner").get_data(as_text=True)
    assert "Organisation Super Admin" in owner
    assert client.get(f"/platform/org/{t}/roles/no_such_role").status_code == 404
