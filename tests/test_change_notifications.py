"""Operators and admins choose whether changes are announced in members' app Notifications; admins edit members."""
import re

from conftest import auth
from test_members_and_phone_rules import member_id
from test_org_content import ADMIN, CLIENT, MEMBER, portal_login, setup_org


def inbox(client, phone):
    token = client.post("/api/login", json={"login": phone, "password": "secret-pass-1"}).get_json()["token"]
    return [(n["title"], n["body"]) for n in client.get("/api/notifications", headers=auth(token)).get_json()["notifications"]
            if n["kind"] == "org"]


def test_notice_notifies_only_when_ticked_and_only_its_audience(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/notices", data={"csrf": csrf, "title": "Quiet one", "body": "no ping"})
    resp = client.post(f"/platform/org/{t}/notices", data={"csrf": csrf, "title": "For clients", "body": "Fees", "notify": "1",
                                                            "audience": ["client"]})
    assert "1 person was notified" in client.get(resp.headers["Location"]).get_data(as_text=True)
    assert ("Test Advocates: For clients", "Fees") in inbox(client, CLIENT)
    assert inbox(client, MEMBER) == []  # not in the audience, and the quiet one pinged nobody


def test_contact_notification_optional(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/emergency", data={"csrf": csrf, "name": "Security desk", "phone": "0720999888", "notify": "1"})
    assert any("New emergency contact in Test Advocates: Security desk: 0720999888" in b for _t, b in inbox(client, MEMBER))
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Reception", "phone": "0720111222"})
    assert not any("Reception" in b for _t, b in inbox(client, MEMBER))


def test_member_changes_notify_when_ticked(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    mid = member_id(client, t, MEMBER)
    client.post(f"/platform/org/{t}/members/{mid}", data={"csrf": csrf, "role": "member", "title": "Driver"})  # not ticked
    assert inbox(client, MEMBER) == []
    resp = client.post(f"/platform/org/{t}/members/{mid}", data={"csrf": csrf, "role": "content_publisher", "title": "Driver",
                                                                  "notify": "1"})
    assert "notified in the app" in client.get(resp.headers["Location"]).get_data(as_text=True)
    assert ("Test Advocates: your details changed", "Your role is now Content Publisher.") in inbox(client, MEMBER)
    client.post(f"/platform/org/{t}/members/{mid}/remove", data={"csrf": csrf, "notify": "1"})
    assert any(title == "Test Advocates: you've been removed" for title, _b in inbox(client, MEMBER))
    client.post(f"/platform/org/{t}/members/{mid}/readd", data={"csrf": csrf})  # not ticked
    assert not any(title == "Test Advocates: you've been added" for title, _b in inbox(client, MEMBER))


def test_new_member_told_or_not(client, register):
    t = setup_org(register)
    register(phone="0715000005")
    _r, csrf = portal_login(client, ADMIN)
    resp = client.post(f"/platform/org/{t}/members", data={"csrf": csrf, "phone": "0715000005", "role": "member", "notify": "1"})
    assert "They were notified" in client.get(resp.headers["Location"]).get_data(as_text=True)
    assert inbox(client, "0715000005")[0][0] == "Test Advocates: you've been added"
    resp = client.post(f"/platform/org/{t}/members", data={"csrf": csrf, "phone": "0716000006", "role": "member", "notify": "1"})
    assert "don&#39;t have the app yet" in client.get(resp.headers["Location"]).get_data(as_text=True)


def test_admin_edits_members_from_organisation_page(staff_client, register):
    t = setup_org(register)
    client, csrf = staff_client()
    client.post(f"/platform/org/{t}/departments", data={"csrf": csrf, "name": "Litigation"})
    page = client.get(f"/platform/tenants/{t}").get_data(as_text=True)
    assert "Manage members and roles" in page and "Groups and departments" in page
    mid = re.search(r"/members\?edit=(\d+)#form", page).group(1)
    edit = client.get(f"/platform/org/{t}/members?edit={mid}").get_data(as_text=True)
    assert "Save changes" in edit and "Tell them about the change in the app" in edit
    dept = re.search(r'value="d:(\d+)"\s*> Litigation', edit).group(1)
    client.post(f"/platform/org/{t}/members/{mid}", data={"csrf": csrf, "role": "department_supervisor", "supervises": dept,
                                                          "placements_sent": "1", "placements": [f"d:{dept}"]})
    page = client.get(f"/platform/tenants/{t}").get_data(as_text=True)
    assert "Litigation (department)" in page  # a name, not "department #2"
