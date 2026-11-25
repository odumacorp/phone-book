"""Organisation contacts in members' own contact lists: per contact, by role/department/group, per person; always live."""
import re

from conftest import app_module, auth, login_ussd
from pb.org import content
from pb.tenancy.repo import TenantRepo
from test_members_and_phone_rules import member_id
from test_org_content import ADMIN, CLIENT, MEMBER, portal_login, setup_org


def their_contacts(client, phone):
    token = client.post("/api/login", json={"login": phone, "password": "secret-pass-1"}).get_json()["token"]
    return {c["name"]: c for c in client.get("/api/contacts", headers=auth(token)).get_json()["contacts"]}


def with_sales(register):
    """An org with a Sales department holding MEMBER."""
    t = setup_org(register)
    with app_module.app.test_request_context():
        repo = TenantRepo(t)
        sales = repo.insert("departments", name="Sales", created_at=0)
        m = repo.one("memberships", "user_phone = ?", ("+254712345678",))
        repo.insert("department_members", returning_id=False, department_id=sales, membership_id=m["id"])
    return t, sales


def contact_id(client, t, name):
    page = client.get(f"/platform/org/{t}/contacts").get_data(as_text=True)
    page = page[page.index('id="contacts-table"'):]  # the table, not the Arrange list above it
    row = page[page.index(f"<b>{name}</b>"):]
    return re.search(r"/contacts/(\d+)/delete", row).group(1)


def test_one_by_one_from_the_contact_form_and_live_edits(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Front desk", "phone": "0720111222", "in_phone_books": "1"})
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Back office", "phone": "0720999000"})
    mine = their_contacts(client, MEMBER)
    assert list(mine) == ["Front desk"] and mine["Front desk"]["org"] == "Test Advocates" and mine["Front desk"]["id"] < 0
    cid = contact_id(client, t, "Front desk")
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "contact_id": cid, "name": "Reception", "phone": "0720111222",
                                                      "in_phone_books": "1"})
    assert list(their_contacts(client, MEMBER)) == ["Reception"]  # the edit shows at once
    page = client.get(f"/platform/org/{t}/contacts").get_data(as_text=True)
    assert "✓ Shown" in page and "1 of 2" in page
    client.post(f"/platform/org/{t}/contacts/{cid}/delete", data={"csrf": csrf})
    assert their_contacts(client, MEMBER) == {}


def test_by_department_group_and_role(client, register):
    t, sales = with_sales(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Sales lead", "phone": "0720222333",
                                                      "in_phone_books": "1", "pb_audience": [f"d:{sales}"]})
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Client helpdesk", "phone": "0720444555",
                                                      "in_phone_books": "1", "pb_audience": ["client"]})
    assert list(their_contacts(client, MEMBER)) == ["Sales lead"]
    assert list(their_contacts(client, CLIENT)) == ["Client helpdesk"]
    page = client.get(f"/platform/org/{t}/contacts").get_data(as_text=True)
    assert "Sales" in page and "Client" in page  # whose lists, by name


def test_who_can_see_by_department(client, register, dial):
    t, sales = with_sales(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/notices", data={"csrf": csrf, "title": "Sales targets", "body": "Q4", "audience": [f"d:{sales}"]})
    with app_module.app.test_request_context():
        assert [n["title"] for n in content.visible_notices(t, "+254712345678")] == ["Sales targets"]
        assert content.visible_notices(t, "+254713000003") == []
        from pb.org.reminders import recipients
        assert recipients(t, content.parse_audience([f"d:{sales}"], t)) == ["+254712345678"]


def test_per_person_from_the_member_page(client, register, dial):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Front desk", "phone": "0720111222", "in_phone_books": "1"})
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Night line", "phone": "0720777888"})
    mid = member_id(client, t, MEMBER)
    edit = client.get(f"/platform/org/{t}/members?edit={mid}").get_data(as_text=True)
    assert "contact list</h2>" in edit and "1 of 2 in their list now" in edit
    front, night = contact_id(client, t, "Front desk"), contact_id(client, t, "Night line")
    # just this person: drop Front desk, add Night line
    client.post(f"/platform/org/{t}/members/{mid}/contacts", data={"csrf": csrf, "contacts": [night]})
    assert list(their_contacts(client, MEMBER)) == ["Night line"]
    assert list(their_contacts(client, CLIENT)) == ["Front desk"]  # everyone else unchanged
    page = client.get(f"/platform/org/{t}/contacts").get_data(as_text=True)
    assert "(not Admin" in page or "(not Member" in page or "not " in page
    # editing the contact keeps the per-person choices
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "contact_id": front, "name": "Front desk", "phone": "0720111222",
                                                      "in_phone_books": "1"})
    assert list(their_contacts(client, MEMBER)) == ["Night line"]
    # back to how it was
    client.post(f"/platform/org/{t}/members/{mid}/contacts", data={"csrf": csrf, "contacts": [front]})
    assert list(their_contacts(client, MEMBER)) == ["Front desk"]
    assert "Night line" not in their_contacts(client, CLIENT)  # it was only ever for that one person
    s = login_ussd(dial, "+254712345678")
    assert "Front desk" in s.send("1")


def test_own_number_wins_and_cant_edit_org_ones(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Front desk", "phone": "0720111222", "in_phone_books": "1"})
    token = client.post("/api/login", json={"login": MEMBER, "password": "secret-pass-1"}).get_json()["token"]
    org_id = next(c["id"] for c in client.get("/api/contacts", headers=auth(token)).get_json()["contacts"])
    assert client.delete(f"/api/contacts/{org_id}", headers=auth(token)).status_code in (404, 400)
    client.post("/api/contacts", json={"name": "My desk friend", "phone": "0720111222"}, headers=auth(token))
    names = [c["name"] for c in client.get("/api/contacts", headers=auth(token)).get_json()["contacts"]]
    assert names == ["My desk friend"]  # their own copy wins; no duplicate


def test_default_lists_by_role_department_and_group(client, register):
    t, sales = with_sales(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Sales lead", "phone": "0720222333"})
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Front desk", "phone": "0720111222", "in_phone_books": "1"})
    lead, front = contact_id(client, t, "Sales lead"), contact_id(client, t, "Front desk")
    page = client.get(f"/platform/org/{t}/contact-lists?for=d:{sales}").get_data(as_text=True)
    assert "Default list for" in page and "Contacts in every Sales list" in page
    # Sales gets the Sales lead; and Front desk (everyone's) stops for Sales only
    client.post(f"/platform/org/{t}/contact-lists", data={"csrf": csrf, "for": f"d:{sales}", "contacts": [lead]})
    assert list(their_contacts(client, MEMBER)) == ["Sales lead"]
    assert list(their_contacts(client, CLIENT)) == ["Front desk"]
    page = client.get(f"/platform/org/{t}/contacts").get_data(as_text=True)
    assert "(not Sales)" in page
    # by role: clients get the Sales lead too
    client.post(f"/platform/org/{t}/contact-lists", data={"csrf": csrf, "for": "client", "contacts": [lead, front]})
    assert set(their_contacts(client, CLIENT)) == {"Sales lead", "Front desk"}
    # untick everything for Sales: the Sales lead goes back to clients only
    client.post(f"/platform/org/{t}/contact-lists", data={"csrf": csrf, "for": f"d:{sales}"})
    assert their_contacts(client, MEMBER) == {}


def test_member_card_reasons_and_add_for_them(client, register):
    t, sales = with_sales(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Front desk", "phone": "0720111222", "in_phone_books": "1"})
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Sales lead", "phone": "0720222333",
                                                      "in_phone_books": "1", "pb_audience": [f"d:{sales}"]})
    mid = member_id(client, t, MEMBER)
    edit = client.get(f"/platform/org/{t}/members?edit={mid}").get_data(as_text=True)
    assert "everyone who can see it" in edit and "for Sales" in edit
    client.post(f"/platform/org/{t}/members/{mid}/contacts/new", data={"csrf": csrf, "name": "Mentor", "phone": "0720555666"})
    assert set(their_contacts(client, MEMBER)) == {"Front desk", "Sales lead", "Mentor"}
    assert "Mentor" not in their_contacts(client, CLIENT)
    edit = client.get(f"/platform/org/{t}/members?edit={mid}").get_data(as_text=True)
    assert "just for them" in edit


def test_clear_only_picked_and_reset(client, register):
    t, sales = with_sales(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Front desk", "phone": "0720111222", "in_phone_books": "1"})
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Sales lead", "phone": "0720222333",
                                                      "in_phone_books": "1", "pb_audience": [f"d:{sales}"]})
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Night line", "phone": "0720777888"})
    mid = member_id(client, t, MEMBER)
    night = contact_id(client, t, "Night line")
    # clear: nothing from the organisation, others unaffected
    client.post(f"/platform/org/{t}/members/{mid}/contacts/clear", data={"csrf": csrf})
    assert their_contacts(client, MEMBER) == {}
    assert list(their_contacts(client, CLIENT)) == ["Front desk"]
    # reset: back to the defaults
    client.post(f"/platform/org/{t}/members/{mid}/contacts/reset", data={"csrf": csrf})
    assert set(their_contacts(client, MEMBER)) == {"Front desk", "Sales lead"}
    # only the contacts picked for them: starts from what they have, then exactly the ticks
    client.post(f"/platform/org/{t}/members/{mid}/contacts/mode", data={"csrf": csrf, "mode": "selected"})
    assert set(their_contacts(client, MEMBER)) == {"Front desk", "Sales lead"}
    client.post(f"/platform/org/{t}/members/{mid}/contacts", data={"csrf": csrf, "contacts": [night]})
    assert list(their_contacts(client, MEMBER)) == ["Night line"]
    assert list(their_contacts(client, CLIENT)) == ["Front desk"]  # nobody else changes
    edit = client.get(f"/platform/org/{t}/members?edit={mid}").get_data(as_text=True)
    assert 'value="selected" checked' in edit and "picked for them" in edit
    # new default contacts don't reach them while they're on "only picked"
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "New helpline", "phone": "0720888999", "in_phone_books": "1"})
    assert list(their_contacts(client, MEMBER)) == ["Night line"]
    client.post(f"/platform/org/{t}/members/{mid}/contacts/clear", data={"csrf": csrf})
    assert their_contacts(client, MEMBER) == {}
    client.post(f"/platform/org/{t}/members/{mid}/contacts/mode", data={"csrf": csrf, "mode": "defaults"})
    assert set(their_contacts(client, MEMBER)) == {"Front desk", "Sales lead", "New helpline"}
