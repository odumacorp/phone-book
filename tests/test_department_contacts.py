"""Department contacts: the admin ticks contacts on a department and everyone in it gets them in their own list."""
import re

from conftest import app_module, login_ussd
from pb.tenancy.repo import TenantRepo
from test_contact_lists import contact_id, their_contacts, with_sales
from test_members_and_phone_rules import member_id
from test_org_content import ADMIN, CLIENT, MEMBER, portal_login


def add_contacts(client, t, csrf, *pairs):
    for name, phone in pairs:
        client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": name, "phone": phone})


def test_assign_contacts_to_a_department(client, register, dial):
    t, sales = with_sales(register)
    _r, csrf = portal_login(client, ADMIN)
    add_contacts(client, t, csrf, ("Sales lead", "0720100100"), ("Price desk", "0720200200"), ("Garage", "0720300300"))
    page = client.get(f"/platform/org/{t}/groups?dept={sales}").get_data(as_text=True)
    assert "Contacts for Sales" in page and "Price desk" in page
    resp = client.post(f"/platform/org/{t}/departments/{sales}/contacts",
                       data={"csrf": csrf, "contacts": [contact_id(client, t, "Sales lead"), contact_id(client, t, "Price desk")]})
    assert resp.status_code == 302
    assert set(their_contacts(client, MEMBER)) == {"Sales lead", "Price desk"}  # MEMBER is in Sales
    assert their_contacts(client, CLIENT) == {}
    assert "2 contacts" in client.get(f"/platform/org/{t}/groups").get_data(as_text=True)
    assert "Price desk" in login_ussd(dial, "+254712345678").send("1")
    edit = client.get(f"/platform/org/{t}/members?edit={member_id(client, t, MEMBER)}").get_data(as_text=True)
    assert "for Sales" in edit
    # The Default contact lists page shows the same picks for Sales
    lists = client.get(f"/platform/org/{t}/contact-lists?for=d:{sales}").get_data(as_text=True)
    assert len(re.findall(r'name="contacts" value="\d+" checked', lists)) == 2
    # Unticking takes them away again
    client.post(f"/platform/org/{t}/departments/{sales}/contacts", data={"csrf": csrf, "contacts": [contact_id(client, t, "Price desk")]})
    assert list(their_contacts(client, MEMBER)) == ["Price desk"]


def test_inner_departments_get_them_and_deleting_the_department_clears_them(client, register):
    t, sales = with_sales(register)
    _r, csrf = portal_login(client, ADMIN)
    add_contacts(client, t, csrf, ("Head office", "0720100100"))
    with app_module.app.test_request_context():
        repo = TenantRepo(t)
        org = repo.insert("departments", name="Company", created_at=0)
        repo.update("departments", "id = ?", (sales,), parent_id=org)
    client.post(f"/platform/org/{t}/departments/{org}/contacts", data={"csrf": csrf, "contacts": [contact_id(client, t, "Head office")]})
    assert list(their_contacts(client, MEMBER)) == ["Head office"]  # Sales is inside Company
    client.post(f"/platform/org/{t}/departments/{org}/delete", data={"csrf": csrf})
    assert their_contacts(client, MEMBER) == {}


def test_only_contacts_they_may_see_and_other_orgs_ignored(client, register):
    t, sales = with_sales(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Admins only", "phone": "0720100100",
                                                      "audience_mode": "some", "audience": ["org_admin"]})
    client.post(f"/platform/org/{t}/departments/{sales}/contacts", data={"csrf": csrf, "contacts": [contact_id(client, t, "Admins only"), "999999"]})
    assert their_contacts(client, MEMBER) == {}
    resp = client.post(f"/platform/org/{t}/departments/999999/contacts", data={"csrf": csrf, "contacts": []})
    assert resp.status_code == 302


def test_pick_contact_groups_on_a_department(client, register):
    from test_contact_groups import make_group
    t, sales = with_sales(register)
    _r, csrf = portal_login(client, ADMIN)
    add_contacts(client, t, csrf, ("Claims desk", "0720100100"), ("Assessor", "0720200200"), ("Night line", "0720300300"))
    gid = make_group(client, t, csrf, "Claims team", ["Claims desk", "Assessor"])  # not given to anyone yet
    make_group(client, t, csrf, "Clients pack", ["Night line"], audience=["client"])
    page = client.get(f"/platform/org/{t}/groups?dept={sales}").get_data(as_text=True)
    assert "Claims team" in page and "Clients pack" in page
    client.post(f"/platform/org/{t}/departments/{sales}/contacts", data={"csrf": csrf, "groups_shown": "1", "contact_groups": [gid]})
    assert set(their_contacts(client, MEMBER)) == {"Claims desk", "Assessor"}
    assert list(their_contacts(client, CLIENT)) == ["Night line"]  # the other group keeps its own people
    assert "Given to: Sales" in client.get(f"/platform/org/{t}/contact-groups").get_data(as_text=True)
    edit = client.get(f"/platform/org/{t}/members?edit={member_id(client, t, MEMBER)}").get_data(as_text=True)
    assert "from contact group Claims team" in edit
    # Unticking takes the department off again; with nobody left it's "not given to anyone yet"
    client.post(f"/platform/org/{t}/departments/{sales}/contacts", data={"csrf": csrf, "groups_shown": "1"})
    assert their_contacts(client, MEMBER) == {}
    assert "Not given to anyone yet" in client.get(f"/platform/org/{t}/contact-groups").get_data(as_text=True)


def test_default_lists_page_follows_the_departments_contact_groups(client, register):
    from test_contact_groups import make_group
    t, sales = with_sales(register)
    _r, csrf = portal_login(client, ADMIN)
    add_contacts(client, t, csrf, ("Claims desk", "0720100100"), ("Garage", "0720300300"))
    gid = make_group(client, t, csrf, "Claims team", ["Claims desk"], audience=[f"d:{sales}"])
    page = client.get(f"/platform/org/{t}/contact-lists?for=d:{sales}").get_data(as_text=True)
    assert "Claims team" in page and "from contact group Claims team" in page
    row = page[page.index("<b>Claims desk</b>") - 400:page.index("<b>Claims desk</b>")]
    assert "checked" in row and "disabled" in row  # comes from the contact group: change it there or untick the group
    other = client.get(f"/platform/org/{t}/contact-lists?for=client").get_data(as_text=True)
    assert "from contact group" not in other
    # Unticking the group on this page takes it away from Sales; ticking a single contact still works
    client.post(f"/platform/org/{t}/contact-lists", data={"csrf": csrf, "for": f"d:{sales}", "groups_shown": "1",
                                                          "contacts": [contact_id(client, t, "Garage")]})
    assert list(their_contacts(client, MEMBER)) == ["Garage"]
    client.post(f"/platform/org/{t}/contact-lists", data={"csrf": csrf, "for": f"d:{sales}", "groups_shown": "1",
                                                          "contact_groups": [gid], "contacts": [contact_id(client, t, "Garage")]})
    assert set(their_contacts(client, MEMBER)) == {"Garage", "Claims desk"}
