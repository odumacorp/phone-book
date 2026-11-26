"""/help: support details, FAQ chat (and the optional Claude path), contact form; the app's Platform button."""
from types import SimpleNamespace

from conftest import app_module, auth, csrf_of
from pb import support
from pb.rbac import engine as rbac
from pb.tenancy import tenants as tenancy


def chat(client, text, history=None, csrf=None):
    csrf = csrf or csrf_of(client, "/help")
    messages = (history or []) + [{"role": "user", "content": text}]
    return client.post("/help/chat", json={"messages": messages}, headers={"X-CSRF-Token": csrf})


def test_help_page_renders_with_faq_and_form(client):
    page = client.get("/help").get_data(as_text=True)
    assert "How can we help?" in page and "*384*88041#" in page
    assert "I forgot my USSD PIN" in page and 'name="message"' in page and "Support assistant" in page
    assert "Never type your PIN" in page


def test_support_details_edited_in_platform_show_on_help(staff_client, client):
    staff, csrf = staff_client()
    staff.post("/platform/settings", data={"csrf": csrf, "action": "support", "support_phone": "0711 222 333",
                                           "support_whatsapp": "0711222333", "support_hours": "Mon-Fri 8-5",
                                           "support_email": "help@oduma.co.ke"})
    page = client.get("/help").get_data(as_text=True)
    assert "0711 222 333" in page and "https://wa.me/254711222333" in page and "help@oduma.co.ke" in page
    assert "Mon-Fri 8-5" in page
    assert "support.details" in staff.get("/platform/audit").get_data(as_text=True)


def test_faq_chat_answers(client):
    csrf = csrf_of(client, "/help")
    assert "Change USSD PIN" in chat(client, "I forgot my PIN", csrf=csrf).get_json()["reply"]
    assert "SMS me my contacts" in chat(client, "how do i get contacts on my new phone", csrf=csrf).get_json()["reply"]
    assert "Sync all" in chat(client, "backup", csrf=csrf).get_json()["reply"]
    assert chat(client, "habari", csrf=csrf).get_json()["reply"] == support.GREETING
    assert chat(client, "zzqx blorp", csrf=csrf).get_json()["reply"] == support.NO_MATCH
    assert chat(client, "I forgot my PIN", csrf=csrf).get_json()["source"] == "faq"


def test_chat_needs_csrf_and_valid_input(client):
    assert client.post("/help/chat", json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 403
    csrf = csrf_of(client, "/help")
    bad = client.post("/help/chat", json={"messages": [{"role": "assistant", "content": "x"}]}, headers={"X-CSRF-Token": csrf})
    assert bad.status_code == 400
    assert client.post("/help/chat", json={"messages": "nope"}, headers={"X-CSRF-Token": csrf}).status_code == 400


def test_chat_rate_limit(client, monkeypatch):
    monkeypatch.setattr(support, "CHAT_PER_WINDOW", 3)
    csrf = csrf_of(client, "/help")
    codes = [chat(client, "pin", csrf=csrf).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


class FakeMessages:
    def __init__(self, stop_reason="end_turn", text="Dial the code and choose 6."):
        self.calls, self.stop_reason, self.text = [], stop_reason, text

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(stop_reason=self.stop_reason, content=[SimpleNamespace(type="text", text=self.text)])


def fake_client(messages):
    return SimpleNamespace(beta=SimpleNamespace(messages=messages))


def test_ai_chat_grounded_and_cached(client, monkeypatch):
    fake = FakeMessages()
    monkeypatch.setattr(support, "ai_enabled", lambda: True)
    monkeypatch.setattr(support, "_anthropic", lambda: fake_client(fake))
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": support.GREETING}]
    body = chat(client, "new phone?", history=history).get_json()
    assert body == {"reply": "Dial the code and choose 6.", "source": "ai"}
    call = fake.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "I forgot my USSD PIN" in call["system"][0]["text"] and "Never ask for" in call["system"][0]["text"]
    assert [m["role"] for m in call["messages"]] == ["user", "assistant", "user"]


def test_ai_refusal_or_error_falls_back_to_faq(client, monkeypatch):
    monkeypatch.setattr(support, "ai_enabled", lambda: True)
    monkeypatch.setattr(support, "_anthropic", lambda: fake_client(FakeMessages(stop_reason="refusal")))
    body = chat(client, "I forgot my PIN").get_json()
    assert body["source"] == "faq" and "Change USSD PIN" in body["reply"]

    import anthropic
    import httpx2

    class Broken:
        def create(self, **kwargs):
            raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    monkeypatch.setattr(support, "_anthropic", lambda: fake_client(Broken()))
    assert chat(client, "I forgot my PIN").get_json()["source"] == "faq"


def test_contact_form_files_a_report(client):
    csrf = csrf_of(client, "/help")
    resp = client.post("/help", data={"csrf": csrf, "name": "Wanjiru", "contact": "0712345678", "topic": "USSD",
                                      "message": "The menu doesn't load on my phone."})
    assert "sent=1" in resp.headers["Location"]
    from pb.db import fetch_one
    with app_module.app.app_context():
        row = fetch_one("SELECT contact, message FROM complaints")
    assert row["contact"] == "0712345678" and "[Help page] USSD - from Wanjiru" in row["message"]
    resp = client.post("/help", data={"csrf": csrf, "contact": "", "message": "Help me please"})
    assert "error=" in resp.headers["Location"]
    assert "error=" in client.post("/help", data={"csrf": "wrong", "contact": "x", "message": "hello there"}).headers["Location"]


def test_honeypot_drops_bots(client):
    csrf = csrf_of(client, "/help")
    client.post("/help", data={"csrf": csrf, "contact": "bot@x.com", "message": "buy cheap stuff", "website": "spam.com"})
    from pb.db import fetch_one
    with app_module.app.app_context():
        assert fetch_one("SELECT COUNT(*) AS n FROM complaints")["n"] == 0


def test_platform_button_only_for_people_who_manage(client, register):
    admin_token = register(phone="0711000001")
    member_token = register(phone="0712000002")
    with app_module.app.test_request_context():
        t = tenancy.create_tenant(rbac.ROOT, "Org", "company", "enterprise_standard")
        for phone, role in (("0711000001", "org_admin"), ("0712000002", "member")):
            tenancy.add_member(rbac.ROOT, t, phone)
            rbac.grant(rbac.ROOT, t, rbac.user_principal("+254" + phone[1:]), role, as_platform=True)
    assert client.get("/api/me", headers=auth(admin_token)).get_json()["platform_access"] is True
    assert client.get("/api/me", headers=auth(member_token)).get_json()["platform_access"] is False


def test_website_hands_its_login_to_platform(client, register):
    token = register(phone="0711000001")
    with app_module.app.test_request_context():
        t = tenancy.create_tenant(rbac.ROOT, "Org", "company", "enterprise_standard")
        tenancy.add_member(rbac.ROOT, t, "0711000001")
        rbac.grant(rbac.ROOT, t, rbac.user_principal("+254711000001"), "org_admin", as_platform=True)
    assert client.post("/platform/app-login").status_code == 401
    assert client.post("/platform/app-login", headers=auth(token)).status_code == 200
    page = client.get("/platform/", follow_redirects=True).get_data(as_text=True)
    assert "Org" in page and "Post a notice" in page


def test_android_download_link(client):
    resp = client.get("/android")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/releases/latest/download/remote-phonebook.apk")
