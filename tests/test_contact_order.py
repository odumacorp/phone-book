"""Arranging contacts: the admin orders the organisation's contacts; each person orders their own."""
import re

from conftest import auth, login_ussd
from test_org_content import ADMIN, MEMBER, portal_login, setup_org


def names(client, token):
    return [c["name"] for c in client.get("/api/contacts", headers=auth(token)).get_json()["contacts"]]


def test_person_arranges_their_own_contacts(client, register, dial):
    token = register()
    for name, phone in (("Alice", "0722000001"), ("Bob", "0722000002"), ("Carol", "0722000003")):
        client.post("/api/contacts", json={"name": name, "phone": phone}, headers=auth(token))
    assert names(client, token) == ["Alice", "Bob", "Carol"]  # not arranged yet: A-Z
    ids = {c["name"]: c["id"] for c in client.get("/api/contacts", headers=auth(token)).get_json()["contacts"]}
    resp = client.post("/api/contacts/order", json={"ids": [ids["Carol"], ids["Alice"], ids["Bob"], 999999]}, headers=auth(token))
    assert resp.get_json()["saved"] == 3  # someone else's (or a made-up) id is ignored
    assert names(client, token) == ["Carol", "Alice", "Bob"]
    client.post("/api/contacts", json={"name": "Aaron", "phone": "0722000004"}, headers=auth(token))
    assert names(client, token)[-1] == "Aaron"  # new contacts go after the arranged ones
    s = login_ussd(dial, "+254712345678")
    screen = s.send("1")
    assert screen.index("Carol") < screen.index("Alice") < screen.index("Bob")  # USSD follows the order
    assert client.post("/api/contacts/order", json={"ids": "nope"}, headers=auth(token)).status_code == 400


def test_admin_arranges_organisation_contacts(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    for name, phone in (("Reception", "0720100100"), ("Accounts", "0720200200"), ("Court clerk", "0720300300")):
        client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": name, "phone": phone})
    page = client.get(f"/platform/org/{t}/contacts").get_data(as_text=True)
    assert "Arrange order" in page
    ids = dict((n, i) for i, n in re.findall(r'<li draggable="true" data-id="(\d+)">.*?<b>([^<]+)</b>', page, re.S))
    order = ",".join([ids["Court clerk"], ids["Reception"], ids["Accounts"]])
    client.post(f"/platform/org/{t}/contacts/order", data={"csrf": csrf, "order": order})
    member = client.post("/api/login", json={"login": MEMBER, "password": "secret-pass-1"}).get_json()["token"]
    seen = [c["name"] for c in client.get(f"/api/orgs/{t}", headers=auth(member)).get_json()["contacts"]]
    assert seen == ["Court clerk", "Reception", "Accounts"]
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Archive", "phone": "0720400400"})
    seen = [c["name"] for c in client.get(f"/api/orgs/{t}", headers=auth(member)).get_json()["contacts"]]
    assert seen[-1] == "Archive"
