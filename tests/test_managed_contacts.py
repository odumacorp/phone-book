"""Organisations choose: members manage their own contacts, or the organisation provides them."""
from conftest import app_module, auth, login_ussd
from pb.contacts import list_contacts

from pb.tenancy import tenants as tenancy
from test_org_content import ADMIN, MEMBER, portal_login, setup_org


def member_token(client):
    return client.post("/api/login", json={"login": MEMBER, "password": "secret-pass-1"}).get_json()["token"]


def managed_org(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/settings", data={"csrf": csrf, "key": "member_contacts", "value": "managed"})
    return t, csrf


def test_default_members_manage_their_own(client, register):
    setup_org(register)
    token = member_token(client)
    assert client.get("/api/me", headers=auth(token)).get_json()["contacts_managed_by"] is None
    assert client.post("/api/contacts", json={"name": "Mum", "phone": "0722000001"}, headers=auth(token)).status_code == 201


def test_managed_blocks_adding_syncing_editing_on_app_and_ussd(client, register, dial):
    t, _csrf = managed_org(client, register)
    token = member_token(client)
    assert client.get("/api/me", headers=auth(token)).get_json()["contacts_managed_by"] == "Test Advocates"
    resp = client.post("/api/contacts", json={"name": "Mum", "phone": "0722000001"}, headers=auth(token))
    assert resp.status_code == 403 and "provided by Test Advocates" in resp.get_json()["error"]
    assert client.post("/api/contacts/sync", json={"contacts": [{"name": "A", "phone": "0711000099"}]},
                       headers=auth(token)).status_code == 403
    assert client.post("/api/contacts/delete", json={"ids": [1]}, headers=auth(token)).status_code == 403
    s = login_ussd(dial, "+254712345678")
    assert "provided by Test Advocates" in s.send("3")
    s = login_ussd(dial, "+254712345678")
    assert "provided by Test Advocates" in s.send("5")
    # the organisation's admins keep their own phone book
    admin = client.post("/api/login", json={"login": ADMIN, "password": "secret-pass-1"}).get_json()["token"]
    assert client.post("/api/contacts", json={"name": "Mum", "phone": "0722000001"}, headers=auth(admin)).status_code == 201


def test_operator_puts_contacts_in_members_phone_books(client, register, dial):
    t, csrf = managed_org(client, register)
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Front desk", "phone": "0720111222"})
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Clients desk", "phone": "0720333444", "audience": ["client"]})
    resp = client.post(f"/platform/org/{t}/contacts/push", data={"csrf": csrf, "notify": "1"})
    assert "Added 4 numbers to 3 members" in client.get(resp.headers["Location"]).get_data(as_text=True)
    with app_module.app.test_request_context():
        assert [c["name"] for c in list_contacts("+254712345678")] == ["Front desk"]  # clients-only one not given to members
        assert {c["name"] for c in list_contacts("+254713000003")} == {"Clients desk", "Front desk"}
    resp = client.post(f"/platform/org/{t}/contacts/push", data={"csrf": csrf})
    assert "already has these numbers" in client.get(resp.headers["Location"]).get_data(as_text=True)
    s = login_ussd(dial, "+254712345678")
    assert "Front desk" in s.send("1")  # in their own phone book on USSD
    titles = [n["title"] for n in client.get("/api/notifications", headers=auth(member_token(client))).get_json()["notifications"]]
    assert "Test Advocates: new numbers in your phone book" in titles


def test_settings_page_and_leaving_the_org_unlocks(client, register):
    t, csrf = managed_org(client, register)
    page = client.get(f"/platform/org/{t}/settings").get_data(as_text=True)
    assert "Members' own contacts" in page and 'value="managed" checked' in page
    with app_module.app.test_request_context():
        member = next(m for m in tenancy.members(t) if m["user_phone"] == "+254712345678")
    client.post(f"/platform/org/{t}/members/{member['id']}/remove", data={"csrf": csrf})
    token = member_token(client)
    assert client.get("/api/me", headers=auth(token)).get_json()["contacts_managed_by"] is None

