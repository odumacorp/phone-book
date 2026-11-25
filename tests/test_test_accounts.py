"""Test accounts: the super admin turns them off (no login anywhere, sessions end) and back on."""
from conftest import app_module, auth, csrf_of, login_ussd
from pb.db import execute

PHONE = "+254712345678"


def mark_test(phone=PHONE):
    with app_module.app.test_request_context():
        execute("UPDATE users SET is_test = 1 WHERE phone = ?", (phone,))


def api_login(client, login="0712345678", password="secret-pass-1"):
    return client.post("/api/login", json={"login": login, "password": password})


def test_super_admin_turns_a_test_account_off_and_on(client, register, staff_client, dial):
    token = register()
    mark_test()
    other = client.application.test_client()
    sc, csrf = staff_client()
    page = sc.get("/platform/test-accounts").get_data(as_text=True)
    assert "0712345678" in page and "Turn off" in page
    resp = sc.post("/platform/test-accounts", data={"csrf": csrf, "action": "user_off", "phone": PHONE})
    assert resp.status_code == 302
    # Every way in is closed, and the open app session has ended
    assert other.get("/api/contacts", headers=auth(token)).status_code == 401
    resp = api_login(other)
    assert resp.status_code == 401 and "turned off" in resp.get_json()["error"]
    s = login_ussd(dial, PHONE)
    assert "turned off" in s.last
    token_form = csrf_of(other)
    resp = other.post("/platform/login", data={"csrf": token_form, "login": "0712345678", "password": "secret-pass-1"})
    assert "turned+off" in resp.headers["Location"] or "turned%20off" in resp.headers["Location"]
    assert "Off" in sc.get("/platform/test-accounts").get_data(as_text=True)
    # Back on: logging in works again (old sessions stay ended)
    sc.post("/platform/test-accounts", data={"csrf": csrf, "action": "user_on", "phone": PHONE})
    assert api_login(other).status_code == 200
    assert other.get("/api/contacts", headers=auth(token)).status_code == 401


def test_turn_all_off_and_on_and_only_test_accounts(client, register, staff_client):
    register()
    register(phone="0722000111")
    mark_test()
    sc, csrf = staff_client()
    # A real account can't be switched from this page
    sc.post("/platform/test-accounts", data={"csrf": csrf, "action": "user_off", "phone": "+254722000111"})
    other = client.application.test_client()
    assert api_login(other, "0722000111").status_code == 200
    sc.post("/platform/test-accounts", data={"csrf": csrf, "action": "all_off"})
    assert api_login(other).status_code == 401
    assert api_login(other, "0722000111").status_code == 200
    sc.post("/platform/test-accounts", data={"csrf": csrf, "action": "all_on"})
    assert api_login(other).status_code == 200


def test_test_staff_accounts_and_who_may_open_the_page(client, staff_client, make_staff):
    with app_module.app.test_request_context():
        helper = make_staff(email="helper@test.oduma.local", roles=("platform_support",))
        execute("UPDATE staff_users SET is_test = 1 WHERE id = ?", (helper,))
    sc, csrf = staff_client()
    assert "helper@test.oduma.local" in sc.get("/platform/test-accounts").get_data(as_text=True)
    sc.post("/platform/test-accounts", data={"csrf": csrf, "action": "staff_off", "staff_id": helper})
    other = client.application.test_client()
    resp = other.post("/platform/login", data={"csrf": csrf_of(other), "email": "helper@test.oduma.local", "password": "staff-pass-123"})
    assert "error" in resp.headers["Location"]
    sc.post("/platform/test-accounts", data={"csrf": csrf, "action": "staff_on", "staff_id": helper})
    resp = other.post("/platform/login", data={"csrf": csrf_of(other), "email": "helper@test.oduma.local", "password": "staff-pass-123"})
    assert resp.headers["Location"].endswith("/platform/")
    assert other.get("/platform/test-accounts").status_code == 403  # support staff can't


def test_test_accounts_are_never_texted_and_skip_login_codes(client, register, fake_sms, ctx):
    from pb.auth.otp import login_needs_otp
    from pb.messaging import send_sms_async, without_test_accounts
    register()
    mark_test()
    before = len(fake_sms.sent)
    send_sms_async(PHONE, "hello")
    assert len(fake_sms.wait(before + 1, timeout=0.3)) == before
    assert without_test_accounts([PHONE, "+254722000999"]) == ["+254722000999"]
    from pb.features import set_feature
    set_feature("otp", True)
    assert not login_needs_otp(PHONE) and login_needs_otp("+254722000999")


def test_deploy_seed_does_nothing_unless_switched_on():
    import os
    import subprocess
    import sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "SEED_TEST_ACCOUNTS": "", "DATABASE_URL": "", "DB_PATH": "/nonexistent/x.db"}
    out = subprocess.run([sys.executable, "scripts/seed_test_accounts.py", "--deploy"], cwd=root, env=env,
                         capture_output=True, text=True)
    assert out.returncode == 0 and "skipped" in out.stdout


def test_back_button_on_portal_pages(staff_client):
    sc, _csrf = staff_client()
    assert 'class="back-btn"' not in sc.get("/platform/").get_data(as_text=True)  # not on the home page
    page = sc.get("/platform/test-accounts").get_data(as_text=True)
    assert 'class="back-btn" href="/platform/"' in page
    page = sc.get("/platform/pricing/plan/enterprise_standard").get_data(as_text=True)
    assert 'class="back-btn" href="/platform/pricing"' in page and "All prices" in page
