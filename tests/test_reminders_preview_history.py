"""Reminders by SMS and app (now and scheduled), the USSD/app preview, and history with repost/restore."""
import datetime
import re

from conftest import app_module, auth
from pb.org import content, reminders
from pb.tenancy.repo import TenantRepo
from test_org_content import ADMIN, MEMBER, portal_login, setup_org

MEMBER_FULL, CLIENT_FULL = "+254712345678", "+254713000003"


def notifications(client, phone):
    token = client.post("/api/login", json={"login": phone, "password": "secret-pass-1"}).get_json()["token"]
    return client.get("/api/notifications", headers=auth(token)).get_json()["notifications"]


# ---------------------------------------------------------------- reminders

def test_send_now_by_sms_and_app(client, register, fake_sms):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    resp = client.post(f"/platform/org/{t}/reminders", data={"csrf": csrf, "title": "Fees due Friday", "body": "Pay by M-Pesa.",
                                                              "channels": "both", "when": "now"})
    flash = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Reminder sent" in flash and "3 in the app" in flash and "3 by SMS" in flash  # admin, member, client
    assert any(m.startswith("Test Advocates: Fees due Friday\nPay by M-Pesa.") for m in fake_sms.to(MEMBER_FULL, count=3))
    titles = [n["title"] for n in notifications(client, MEMBER)]
    assert "Test Advocates: Fees due Friday" in titles


def test_audience_limits_who_gets_it(client, register, fake_sms):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/reminders", data={"csrf": csrf, "title": "Clients only", "body": "Hearing moved.",
                                                      "channels": "sms", "when": "now", "audience": ["client"]})
    sent = fake_sms.wait(1)
    assert [to for to, m in sent if "Clients only" in m] == [CLIENT_FULL]
    with app_module.app.test_request_context():
        assert reminders.recipients(t, content.parse_audience(["client"], t)) == [CLIENT_FULL]


def test_app_only_does_not_send_sms(client, register, fake_sms):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    before = len(fake_sms.sent)
    client.post(f"/platform/org/{t}/reminders", data={"csrf": csrf, "title": "Hi", "body": "App only", "channels": "app", "when": "now"})
    assert len(fake_sms.wait(before)) == before
    assert any(n["body"] == "App only" for n in notifications(client, MEMBER))


def test_scheduled_reminder_sent_by_job_once(client, register, fake_sms):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    later = (datetime.datetime.now(content.EAT) + datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    resp = client.post(f"/platform/org/{t}/reminders", data={"csrf": csrf, "title": "Meeting", "body": "At 5pm.", "channels": "app",
                                                              "when": "later", "send_at": later})
    assert "scheduled for" in client.get(resp.headers["Location"]).get_data(as_text=True)
    assert not any(n["title"].endswith("Meeting") for n in notifications(client, MEMBER))
    with app_module.app.test_request_context():
        TenantRepo(t).update("reminders", "title = ?", ("Meeting",), send_at=1)  # its time comes
    client.post("/internal/tick", headers={"X-Tick-Secret": "tick-secret"})
    client.post("/internal/tick", headers={"X-Tick-Secret": "tick-secret"})
    assert [n["title"] for n in notifications(client, MEMBER)].count("Test Advocates: Meeting") == 1


def test_cancel_scheduled_and_validation(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    later = (datetime.datetime.now(content.EAT) + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    client.post(f"/platform/org/{t}/reminders", data={"csrf": csrf, "title": "X", "body": "Y", "channels": "app", "when": "later", "send_at": later})
    page = client.get(f"/platform/org/{t}/reminders").get_data(as_text=True)
    rid = re.search(r"/reminders/(\d+)/cancel", page).group(1)
    client.post(f"/platform/org/{t}/reminders/{rid}/cancel", data={"csrf": csrf})
    assert "Cancelled" in client.get(f"/platform/org/{t}/reminders").get_data(as_text=True)
    resp = client.post(f"/platform/org/{t}/reminders", data={"csrf": csrf, "title": "X", "body": "Y", "channels": "app",
                                                              "when": "later", "send_at": "2001-01-01T10:00"})
    assert "passed" in client.get(resp.headers["Location"]).get_data(as_text=True)


def test_estimate_and_remind_from_notice(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    est = client.post(f"/platform/org/{t}/reminders/estimate", json={"channels": "both", "audience": [], "title": "Hi", "body": "x" * 200},
                      headers={"X-CSRF-Token": csrf}).get_json()
    assert est["people"] == 3 and est["sms_parts"] == 2 and est["sms_total"] == 6
    client.post(f"/platform/org/{t}/notices", data={"csrf": csrf, "title": "AGM Saturday", "body": "10am, main hall"})
    page = client.get(f"/platform/org/{t}/notices").get_data(as_text=True)
    notice_id = re.search(r"/reminders\?notice=(\d+)", page).group(1)
    form = client.get(f"/platform/org/{t}/reminders?notice={notice_id}").get_data(as_text=True)
    assert 'value="AGM Saturday"' in form and "10am, main hall" in form


def test_member_role_cannot_send_reminders(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, MEMBER)
    assert client.post(f"/platform/org/{t}/reminders", data={"csrf": csrf, "title": "X", "body": "Y"}).status_code == 404


# ---------------------------------------------------------------- preview

def test_preview_ussd_session_as_roles(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/notices", data={"csrf": csrf, "title": "For all", "body": "Hello everyone"})
    client.post(f"/platform/org/{t}/notices", data={"csrf": csrf, "title": "Clients only", "body": "x", "audience": ["client"]})
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Front desk", "phone": "0720111222"})

    def ussd(inputs, role="member"):
        return client.post(f"/platform/org/{t}/preview/ussd", json={"inputs": inputs, "role": role},
                           headers={"X-CSRF-Token": csrf}).get_json()["screen"]

    assert "1. Notices (1)" in ussd([])
    assert "Notices (2)" in ussd([], role="client")
    assert "For all" in ussd(["1"]) and "Clients only" not in ussd(["1"])
    assert "Hello everyone" in ussd(["1", "1"])
    assert "Preview: nothing was sent" in ussd(["1", "1", "1"])
    assert "1. Call\n2. Send SMS" in ussd(["2", "1"])
    assert "no call made" in ussd(["2", "1", "1"])
    page = client.get(f"/platform/org/{t}/preview?role=client").get_data(as_text=True)
    assert "Clients only" in page and "Front desk" in page and "In the app and website" in page


def test_draft_notice_preview_screens(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    data = client.post(f"/platform/org/{t}/preview/notice", json={"title": "Long one", "body": "word " * 80, "urgent": True},
                       headers={"X-CSRF-Token": csrf}).get_json()
    assert data["pages"] >= 3 and len(data["screens"]) == data["pages"] + 1
    assert "1. !Long one" in data["screens"][0] and "98. Next (1/" in data["screens"][1]
    assert all(len(s) <= 182 for s in data["screens"])


# ---------------------------------------------------------------- history

def test_deleted_notice_goes_to_history_and_can_be_reposted(client, register, dial):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/notices", data={"csrf": csrf, "title": "Water outage", "body": "Carry water"})
    page = client.get(f"/platform/org/{t}/notices").get_data(as_text=True)
    nid = re.search(r"/notices/(\d+)/delete", page).group(1)
    client.post(f"/platform/org/{t}/notices/{nid}/delete", data={"csrf": csrf})
    with app_module.app.test_request_context():
        assert content.visible_notices(t, MEMBER_FULL) == []
    history = client.get(f"/platform/org/{t}/history").get_data(as_text=True)
    assert "Deleted notices" in history and "Water outage" in history
    client.post(f"/platform/org/{t}/history/notices/{nid}/repost", data={"csrf": csrf, "expires_mode": "7"})
    with app_module.app.test_request_context():
        live = content.visible_notices(t, MEMBER_FULL)
        assert [n["title"] for n in live] == ["Water outage"] and live[0]["expires_at"]
    assert "No deleted notices" in client.get(f"/platform/org/{t}/history").get_data(as_text=True)


def test_deleted_contact_restore_and_purge(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/contacts", data={"csrf": csrf, "name": "Front desk", "phone": "0720111222"})
    page = client.get(f"/platform/org/{t}/contacts").get_data(as_text=True)
    cid = re.search(r"/contacts/(\d+)/delete", page).group(1)
    client.post(f"/platform/org/{t}/contacts/{cid}/delete", data={"csrf": csrf})
    assert "Front desk" in client.get(f"/platform/org/{t}/history").get_data(as_text=True)
    client.post(f"/platform/org/{t}/history/contacts/{cid}/restore", data={"csrf": csrf})
    assert "Front desk" in client.get(f"/platform/org/{t}/contacts").get_data(as_text=True)
    resp = client.post(f"/platform/org/{t}/history/org_contacts/{cid}/purge", data={"csrf": csrf})
    assert "Only items in the history" in client.get(resp.headers["Location"]).get_data(as_text=True)  # live items can't be purged
    client.post(f"/platform/org/{t}/contacts/{cid}/delete", data={"csrf": csrf})
    client.post(f"/platform/org/{t}/history/org_contacts/{cid}/purge", data={"csrf": csrf})
    with app_module.app.test_request_context():
        assert TenantRepo(t).one("org_contacts", "id = ?", (int(cid),)) is None
    actions = client.get(f"/platform/org/{t}/history").get_data(as_text=True)
    assert "No deleted contacts" in actions
