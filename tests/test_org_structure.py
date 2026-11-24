"""Operators create their own departments and groups; contact forms offer lists for groups and titles."""
import re

from conftest import app_module
from pb.rbac import engine as rbac
from pb.tenancy import tenants as tenancy
from test_org_content import ADMIN, portal_login, setup_org


def school(register):
    return setup_org(register, edition="schools", name="Hill School")


def test_add_departments_and_groups(client, register):
    t = school(register)
    _r, csrf = portal_login(client, ADMIN)
    page = client.get(f"/platform/org/{t}/groups").get_data(as_text=True)
    assert "Groups and departments" in page and "+ Bursar&#39;s office" in page and "+ Form 2" in page  # quick add
    client.post(f"/platform/org/{t}/departments", data={"csrf": csrf, "name": "Academics"})
    page = client.get(f"/platform/org/{t}/groups").get_data(as_text=True)
    academics = re.search(r'<option value="(\d+)"[^>]*>Academics</option>', page).group(1)
    client.post(f"/platform/org/{t}/departments", data={"csrf": csrf, "name": "Sciences", "parent_id": academics})
    client.post(f"/platform/org/{t}/groups", data={"csrf": csrf, "name": "Form 2 East", "kind": "stream", "department_id": academics})
    page = client.get(f"/platform/org/{t}/groups").get_data(as_text=True)
    assert "inside Academics" in page and "Form 2 East" in page and "Stream" in page
    resp = client.post(f"/platform/org/{t}/departments", data={"csrf": csrf, "name": "academics"})
    assert "already a department" in client.get(resp.headers["Location"]).get_data(as_text=True)
    # a department can't go inside its own sub-department
    sciences = re.search(r'<option value="(\d+)"[^>]*>Sciences</option>', page).group(1)
    resp = client.post(f"/platform/org/{t}/departments", data={"csrf": csrf, "department_id": academics, "name": "Academics",
                                                               "parent_id": sciences})
    assert "own sub-departments" in client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "department.add" in str(_actions(t)) and "group.add" in str(_actions(t))


def test_contact_form_lists_groups_and_titles_for_the_kind_of_organisation(client, register):
    t = school(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/departments", data={"csrf": csrf, "name": "Boarding office"})
    page = client.get(f"/platform/org/{t}/contacts").get_data(as_text=True)
    assert 'optgroup label="Your departments and groups"' in page and ">Boarding office</option>" in page
    assert 'optgroup label="Suggested for you"' in page and ">Bursar&#39;s office</option>" in page
    assert ">Principal</option>" in page and ">Deputy principal</option>" in page  # school titles
    assert "Other… (type your own)" in page
    with app_module.app.test_request_context():
        sacco = tenancy.create_tenant(rbac.ROOT, "Pesa SACCO", "sacco", "enterprise_standard")
        tenancy.add_member(rbac.ROOT, sacco, ADMIN)
        rbac.grant(rbac.ROOT, sacco, rbac.user_principal("+254" + ADMIN[1:]), "org_admin", as_platform=True)
    page = client.get(f"/platform/org/{sacco}/contacts").get_data(as_text=True)
    assert ">Loans officer</option>" in page and ">Credit committee</option>" in page and ">Principal</option>" not in page


def test_typed_title_and_group_are_kept_and_offered_again(client, register):
    t = school(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Mr Kamau", "phone": "0720111222",
                                                      "title": "Hostel master", "group_name": "Boys' hostel"})
    page = client.get(f"/platform/org/{t}/contacts").get_data(as_text=True)
    assert 'optgroup label="Used on contacts"' in page and ">Hostel master</option>" in page and ">Boys&#39; hostel</option>" in page
    edit = client.get(f"/platform/org/{t}/contacts?edit=" + re.search(r"\?edit=(\d+)", page).group(1)).get_data(as_text=True)
    assert "<option selected>Hostel master</option>" in edit


def test_emergency_names_suggested(client, register):
    t = school(register)
    portal_login(client, ADMIN)
    page = client.get(f"/platform/org/{t}/emergency").get_data(as_text=True)
    assert '<option value="School nurse">' in page and '<option value="Kenya Red Cross">' in page


def test_member_placed_in_department_counts_for_supervisor(client, register):
    t = school(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/departments", data={"csrf": csrf, "name": "Sciences"})
    page = client.get(f"/platform/org/{t}/members").get_data(as_text=True)
    sciences = re.search(r'<option value="d:(\d+)">Sciences</option>', page).group(1)
    register(phone="0716000006")
    client.post(f"/platform/org/{t}/members", data={"csrf": csrf, "phone": "0716000006", "name": "Lab tech",
                                                     "role": "member", "placement": f"d:{sciences}"})
    page = client.get(f"/platform/org/{t}/members").get_data(as_text=True)
    assert re.search(r"Lab tech.*?Sciences", page, re.S)
    with app_module.app.test_request_context():
        sup = rbac.user_principal("+254717000007")
        tenancy.add_member(rbac.ROOT, t, "0717000007")
        rbac.grant(rbac.ROOT, t, sup, "department_supervisor", ("department", int(sciences)), as_platform=True)
        member = next(m for m in tenancy.members(t) if m["user_phone"] == "+254716000006")
        assert rbac.can(sup, "members.manage", t, ("member", member["id"]))


def test_publishers_cannot_manage_groups(client, register):
    t = school(register)
    register(phone="0718000008")
    with app_module.app.test_request_context():
        tenancy.add_member(rbac.ROOT, t, "0718000008")
        rbac.grant(rbac.ROOT, t, rbac.user_principal("+254718000008"), "content_publisher", as_platform=True)
    _r, csrf = portal_login(client, "0718000008")
    assert client.get(f"/platform/org/{t}/groups").status_code == 403
    assert client.post(f"/platform/org/{t}/departments", data={"csrf": csrf, "name": "X"}).status_code == 403


def _actions(tenant_id):
    from pb.audit import audit_entries
    with app_module.app.app_context():
        return [r["action"] for r in audit_entries(tenant_id=tenant_id)]
