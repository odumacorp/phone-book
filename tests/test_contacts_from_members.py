"""Admins add contacts by picking existing members (one to fill the form, or several at once) or by typing them."""
import re

from conftest import app_module, auth
from pb.org import content
from pb.tenancy.repo import TenantRepo
from test_org_content import ADMIN, MEMBER, portal_login, setup_org


def test_member_picker_and_bulk_add(client, register):
    t = setup_org(register)
    with app_module.app.test_request_context():
        repo = TenantRepo(t)
        dept = repo.insert("departments", name="Claims", created_at=0)
        m = repo.one("memberships", "user_phone = ?", ("+254712345678",))
        repo.update("memberships", "id = ?", (m["id"],), title="Claims officer", display_name="Wanjiku")
        repo.insert("department_members", returning_id=False, department_id=dept, membership_id=m["id"])
    _r, csrf = portal_login(client, ADMIN)
    page = client.get(f"/platform/org/{t}/contacts").get_data(as_text=True)
    assert "Fill in from a member" in page and "Add several members at once" in page
    assert "Wanjiku · 0712345678 · Claims officer · Claims" in page
    ids = re.findall(r'name="members" value="(\d+)"', page)
    assert len(ids) == 3  # admin, member, client
    resp = client.post(f"/platform/org/{t}/contacts/from-members", data={"csrf": csrf, "members": ids, "group_mode": "own"})
    assert "Added 3 contacts" in client.get(resp.headers["Location"]).get_data(as_text=True)
    with app_module.app.test_request_context():
        rows = {c["phone"]: c for c in content.contacts(t, emergency=False)}
    assert rows["+254712345678"]["name"] == "Wanjiku" and rows["+254712345678"]["title"] == "Claims officer"
    assert rows["+254712345678"]["group_name"] == "Claims"
    page = client.get(f"/platform/org/{t}/contacts").get_data(as_text=True)
    assert "Add several members at once" not in page  # everyone is already a contact
    # adding the same members again is skipped, not duplicated
    resp = client.post(f"/platform/org/{t}/contacts/from-members", data={"csrf": csrf, "members": ids})
    assert "already on the list" in client.get(resp.headers["Location"]).get_data(as_text=True)


def test_bulk_into_one_group_and_emergency_list_separate(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    page = client.get(f"/platform/org/{t}/contacts").get_data(as_text=True)
    ids = re.findall(r'name="members" value="(\d+)"', page)
    client.post(f"/platform/org/{t}/contacts/from-members", data={"csrf": csrf, "members": ids[:2], "group_mode": "one",
                                                                   "group_name": "Front office"})
    with app_module.app.test_request_context():
        assert {c["group_name"] for c in content.contacts(t, emergency=False)} == {"Front office"}
    emergency_page = client.get(f"/platform/org/{t}/emergency").get_data(as_text=True)
    assert len(re.findall(r'name="members" value="', emergency_page)) == 3  # the emergency list is its own list
    client.post(f"/platform/org/{t}/emergency/from-members", data={"csrf": csrf, "members": ids[:1]})
    with app_module.app.test_request_context():
        assert len(content.contacts(t, emergency=True)) == 1


def test_one_summary_notification(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    ids = re.findall(r'name="members" value="(\d+)"', client.get(f"/platform/org/{t}/contacts").get_data(as_text=True))
    client.post(f"/platform/org/{t}/contacts/from-members", data={"csrf": csrf, "members": ids, "notify": "1"})
    token = client.post("/api/login", json={"login": MEMBER, "password": "secret-pass-1"}).get_json()["token"]
    titles = [n["title"] for n in client.get("/api/notifications", headers=auth(token)).get_json()["notifications"] if n["kind"] == "org"]
    assert titles == ["Test Advocates: 3 new contacts"]


def test_needs_ticks_and_permission(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    resp = client.post(f"/platform/org/{t}/contacts/from-members", data={"csrf": csrf})
    assert "Tick at least one" in client.get(resp.headers["Location"]).get_data(as_text=True)
    _r, csrf = portal_login(client, MEMBER)
    assert client.post(f"/platform/org/{t}/contacts/from-members", data={"csrf": csrf, "members": ["1"]}).status_code == 404
