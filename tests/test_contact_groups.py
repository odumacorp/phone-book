"""Contact groups: sets of contacts the admin gives to departments, groups or roles (into members' own lists)."""
import re

from conftest import app_module, login_ussd
from pb.tenancy.repo import TenantRepo
from test_contact_lists import contact_id, their_contacts, with_sales
from test_members_and_phone_rules import member_id
from test_org_content import ADMIN, CLIENT, MEMBER, portal_login


def make_group(client, t, csrf, name, contact_names, audience=None):
    resp = client.post(f"/platform/org/{t}/contact-groups", data={"csrf": csrf, "name": name})
    gid = re.search(r"group=(\d+)", resp.headers["Location"]).group(1)
    data = {"csrf": csrf, "contacts": [contact_id(client, t, n) for n in contact_names]}
    if audience is not None:
        data.update({"given": "1", "audience": audience})
    client.post(f"/platform/org/{t}/contact-groups/{gid}", data=data)
    return gid


def test_give_a_contact_group_to_a_department(client, register, dial):
    t, sales = with_sales(register)
    _r, csrf = portal_login(client, ADMIN)
    for name, phone in (("Claims desk", "0720100100"), ("Assessor", "0720200200"), ("Garage", "0720300300")):
        client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": name, "phone": phone})
    make_group(client, t, csrf, "Claims team", ["Claims desk", "Assessor"], audience=[f"d:{sales}"])
    assert set(their_contacts(client, MEMBER)) == {"Claims desk", "Assessor"}  # MEMBER is in Sales
    assert their_contacts(client, CLIENT) == {}
    page = client.get(f"/platform/org/{t}/contact-groups").get_data(as_text=True)
    assert "Claims team" in page and "Given to: Sales" in page and "1 person" in page
    s = login_ussd(dial, "+254712345678")
    assert "Assessor" in s.send("1")
    mid = member_id(client, t, MEMBER)
    edit = client.get(f"/platform/org/{t}/members?edit={mid}").get_data(as_text=True)
    assert "from contact group Claims team" in edit


def test_not_given_until_chosen_and_by_role_or_group(client, register):
    t, sales = with_sales(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Helpline", "phone": "0720100100"})
    gid = make_group(client, t, csrf, "Clients pack", ["Helpline"])  # not given to anyone yet
    assert their_contacts(client, CLIENT) == {}
    client.post(f"/platform/org/{t}/contact-groups/{gid}", data={"csrf": csrf, "contacts": [contact_id(client, t, "Helpline")],
                                                                  "given": "1", "audience": ["client"]})
    assert list(their_contacts(client, CLIENT)) == ["Helpline"]
    with app_module.app.test_request_context():
        grp = TenantRepo(t).insert("member_groups", name="VIP", kind="segment", created_at=0)
        m = TenantRepo(t).one("memberships", "user_phone = ?", ("+254712345678",))
        TenantRepo(t).insert("group_members", returning_id=False, group_id=grp, membership_id=m["id"])
    client.post(f"/platform/org/{t}/contact-groups/{gid}", data={"csrf": csrf, "contacts": [contact_id(client, t, "Helpline")],
                                                                  "given": "1", "audience": [f"g:{grp}"]})
    assert list(their_contacts(client, MEMBER)) == ["Helpline"] and their_contacts(client, CLIENT) == {}


def test_leave_one_person_out_and_only_picked(client, register):
    t, sales = with_sales(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Claims desk", "phone": "0720100100"})
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Night line", "phone": "0720777888"})
    make_group(client, t, csrf, "Everyone pack", ["Claims desk"], audience=[])  # everyone
    mid = member_id(client, t, MEMBER)
    client.post(f"/platform/org/{t}/members/{mid}/contacts", data={"csrf": csrf})  # untick everything for MEMBER
    assert their_contacts(client, MEMBER) == {}
    assert list(their_contacts(client, CLIENT)) == ["Claims desk"]  # others keep it
    client.post(f"/platform/org/{t}/members/{mid}/contacts", data={"csrf": csrf, "contacts": [contact_id(client, t, "Claims desk")]})
    assert list(their_contacts(client, MEMBER)) == ["Claims desk"]
    client.post(f"/platform/org/{t}/members/{mid}/contacts/mode", data={"csrf": csrf, "mode": "selected"})
    client.post(f"/platform/org/{t}/members/{mid}/contacts", data={"csrf": csrf, "contacts": [contact_id(client, t, "Night line")]})
    assert list(their_contacts(client, MEMBER)) == ["Night line"]  # only picked: contact groups don't add


def test_delete_group_and_permissions(client, register):
    t, sales = with_sales(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Claims desk", "phone": "0720100100"})
    gid = make_group(client, t, csrf, "Pack", ["Claims desk"], audience=[])
    client.post(f"/platform/org/{t}/contact-groups/{gid}/delete", data={"csrf": csrf})
    assert their_contacts(client, CLIENT) == {}
    portal_login(client, MEMBER)
    assert client.get(f"/platform/org/{t}/contact-groups").status_code == 404
