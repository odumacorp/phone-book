"""At sign-up people choose an organisation (from the list or by code) or none (an individual account)."""
import re

from conftest import app_module, auth
from pb.rbac import engine as rbac
from pb.tenancy import tenants as tenancy
from test_org_content import ADMIN, portal_login, setup_org


def signup(client, fake_sms, phone, **org):
    resp = client.post("/api/register/request", json={"phone": phone})
    assert resp.status_code == 200, resp.get_json()
    full = resp.get_json()["phone"]
    code = next(m for to, m in reversed(fake_sms.wait()) if to == full).split(" code is ")[1][:6]
    return client.post("/api/register/confirm", json={"phone": phone, "code": code, "name": "Newcomer",
                                                       "password": "secret-pass-1", "pin": "1234", **org})


def test_public_list_hides_invite_only(client, register):
    t = setup_org(register)
    with app_module.app.test_request_context():
        hidden = tenancy.create_tenant(rbac.ROOT, "Secret Club", "company", "enterprise_standard")
        tenancy.set_setting(rbac.ROOT, hidden, "join_mode", "closed")
    names = [o["name"] for o in client.get("/api/orgs/public").get_json()["orgs"]]
    assert "Test Advocates" in names and "Secret Club" not in names
    assert client.get("/api/orgs/public").get_json()["orgs"][0].keys() == {"id", "name", "kind"}  # nothing private
    del t


def test_individual_signup_joins_nothing(client, fake_sms):
    body = signup(client, fake_sms, "0705000001").get_json()
    assert "organisation" not in body
    assert client.get("/api/me", headers=auth(body["token"])).get_json()["memberships"] == []


def test_signup_needs_approval_then_admin_approves(client, register, fake_sms):
    t = setup_org(register)  # join_mode defaults to approval
    body = signup(client, fake_sms, "0705000002", org_id=t).get_json()
    assert body["organisation"] == {"name": "Test Advocates", "status": "pending"}
    assert client.get("/api/me", headers=auth(body["token"])).get_json()["memberships"] == []  # nothing until approved
    _r, csrf = portal_login(client, ADMIN)
    page = client.get(f"/platform/org/{t}/members").get_data(as_text=True)
    assert "Join requests" in page and "0705000002" in page
    mid = re.search(r"/members/(\d+)/approve", page).group(1)
    client.post(f"/platform/org/{t}/members/{mid}/approve", data={"csrf": csrf, "role": "member", "notify": "1"})
    me = client.get("/api/me", headers=auth(body["token"])).get_json()
    assert [m["name"] for m in me["memberships"]] == ["Test Advocates"]
    titles = [n["title"] for n in client.get("/api/notifications", headers=auth(body["token"])).get_json()["notifications"]]
    assert "Test Advocates: you've been added" in titles
    admin_token = client.post("/api/login", json={"login": ADMIN, "password": "secret-pass-1"}).get_json()["token"]
    admin_titles = [n["title"] for n in client.get("/api/notifications", headers=auth(admin_token)).get_json()["notifications"]]
    assert "Test Advocates: request to join" in admin_titles


def test_decline(client, register, fake_sms):
    t = setup_org(register)
    token = signup(client, fake_sms, "0705000003", org_id=t).get_json()["token"]
    _r, csrf = portal_login(client, ADMIN)
    mid = re.search(r"/members/(\d+)/decline", client.get(f"/platform/org/{t}/members").get_data(as_text=True)).group(1)
    client.post(f"/platform/org/{t}/members/{mid}/decline", data={"csrf": csrf, "notify": "1"})
    assert "Join requests" not in client.get(f"/platform/org/{t}/members").get_data(as_text=True)
    titles = [n["title"] for n in client.get("/api/notifications", headers=auth(token)).get_json()["notifications"]]
    assert "Test Advocates: request not approved" in titles


def test_open_org_joins_straight_away(client, register, fake_sms):
    t = setup_org(register)
    with app_module.app.test_request_context():
        tenancy.set_setting(rbac.ROOT, t, "join_mode", "open")
    body = signup(client, fake_sms, "0705000004", org_id=t).get_json()
    assert body["organisation"]["status"] == "member"
    with app_module.app.test_request_context():
        assert rbac.role_keys(rbac.user_principal("+254705000004"), t) == {"member"}


def test_invite_only_by_code_and_bad_code(client, register, fake_sms):
    t = setup_org(register)
    with app_module.app.test_request_context():
        tenancy.set_setting(rbac.ROOT, t, "join_mode", "closed")
        from pb.billing import ussd_codes
        main = next(c for c in ussd_codes.codes() if c["is_main"])
        ussd_codes.assign(rbac.ROOT, t, main["id"], "4242")
    assert signup(client, fake_sms, "0705000005", org_id=t).status_code == 400  # not listed: the id doesn't work
    body = signup(client, fake_sms, "0705000006", org_code="4242").get_json()
    assert body["organisation"]["status"] == "pending"
    assert signup(client, fake_sms, "0705000007", org_code="9999").status_code == 400


def test_already_added_by_the_organisation(client, register, fake_sms):
    t = setup_org(register)
    with app_module.app.test_request_context():
        tenancy.add_member(rbac.ROOT, t, "0705000008", "Pre-added")
    body = signup(client, fake_sms, "0705000008", org_id=t).get_json()
    assert body["organisation"]["status"] == "already"
    assert [m["name"] for m in client.get("/api/me", headers=auth(body["token"])).get_json()["memberships"]] == ["Test Advocates"]


def test_join_later_from_the_app(client, register):
    t = setup_org(register)
    token = register(phone="0705000009")
    resp = client.post("/api/orgs/join", json={"org_id": t}, headers=auth(token))
    assert resp.get_json()["organisation"]["status"] == "pending"
