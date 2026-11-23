"""The platform portal: login, permissions per role, editing prices, organisations and USSD codes."""
import re

from conftest import app_module, csrf_of
from pb.billing import catalogue, ussd_codes
from pb.tenancy import tenants as tenancy


def test_login_required(client):
    resp = client.get("/platform/tenants")
    assert resp.status_code == 302 and "/platform/login" in resp.headers["Location"]


def test_staff_login_and_lockout(client, make_staff):
    make_staff()
    token = csrf_of(client)
    for _ in range(5):
        resp = client.post("/platform/login", data={"csrf": token, "email": "boss@oduma.co.ke", "password": "nope"})
    assert "Too+many" in resp.headers["Location"] or "Too%20many" in resp.headers["Location"]
    resp = client.post("/platform/login", data={"csrf": token, "email": "boss@oduma.co.ke", "password": "staff-pass-123"})
    assert "Too" in resp.headers["Location"]  # locked even with the right password


def test_admin_password_is_break_glass_super_admin(client):
    page = client.get("/admin").get_data(as_text=True)
    token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    client.post("/admin/login", data={"csrf": token, "password": "admin-pass"})
    resp = client.get("/platform/")
    assert resp.status_code == 200 and "Create staff accounts" in resp.get_data(as_text=True)
    token = csrf_of(client, "/platform/")
    resp = client.post("/platform/staff", data={"csrf": token, "name": "Asha", "email": "asha@oduma.co.ke",
                                                "password": "long-password-1", "roles": ["platform_super_admin"]})
    assert resp.status_code == 302
    assert "asha@oduma.co.ke" in client.get("/platform/staff").get_data(as_text=True)


def test_post_needs_csrf(staff_client):
    client, _csrf = staff_client()
    assert client.post("/platform/pricing/plan/enterprise_pro", data={"name": "x"}).status_code == 403


def test_super_admin_edits_prices_from_the_list(staff_client):
    client, csrf = staff_client()
    page = client.get("/platform/pricing").get_data(as_text=True)
    assert "Enterprise Starter" in page and "KES 2,500/month incl. 50 members, then KES 30/member" in page
    assert "Dedicated shortcode" in page and "Setup KES 25,000, then KES 15,000/month" in page
    resp = client.post("/platform/pricing/plan/enterprise_starter", data={
        "csrf": csrf, "name": "Enterprise Starter", "model": "seat", "billing_interval": "month", "base_price": "3,000",
        "included_members": "60", "per_member_price": "28", "annual_discount_pct": "15", "trial_days": "14",
        "active": "1", "sort": "40",
    })
    assert resp.status_code == 302
    with app_module.app.app_context():
        p = catalogue.plan("enterprise_starter")
    assert (p["base_price"], p["included_members"], p["per_member_price"], p["annual_discount_pct"]) == (300000, 60, 2800, 15)
    detail = client.get("/platform/pricing/plan/enterprise_starter").get_data(as_text=True)
    assert "250000" in detail and "300000" in detail  # history shows old and new
    resp = client.post("/platform/pricing/code_fee/dedicated", data={"csrf": csrf, "name": "Dedicated shortcode",
                                                                      "setup_fee": "30000", "monthly_fee": "abc", "note": ""})
    assert "isn" in client.get(resp.headers["Location"]).get_data(as_text=True)  # error shown, nothing saved
    with app_module.app.app_context():
        assert catalogue.code_fee("dedicated")["setup_fee"] == 2500000


def test_support_can_view_but_not_edit_prices(staff_client):
    client, csrf = staff_client(roles=["platform_support"], email="help@oduma.co.ke")
    assert client.get("/platform/pricing").status_code == 200
    assert client.post("/platform/pricing/plan/enterprise_pro", data={"csrf": csrf, "model": "seat"}).status_code == 403
    assert client.get("/platform/staff").status_code == 403
    assert client.get("/platform/settings").status_code == 403


def test_finance_cannot_manage_tenants(staff_client):
    client, csrf = staff_client(roles=["platform_finance"], email="money@oduma.co.ke")
    assert client.get("/platform/tenants").status_code == 200
    assert client.post("/platform/tenants", data={"csrf": csrf, "name": "X", "edition": "company", "plan": "enterprise_pro"}).status_code == 403


def test_create_tenant_assign_code_and_settings(staff_client):
    client, csrf = staff_client()
    resp = client.post("/platform/tenants", data={"csrf": csrf, "name": "Kilimani Academy", "edition": "schools",
                                                  "plan": "enterprise_standard", "owner_phone": "0711222333", "owner_name": "Head"})
    assert resp.status_code == 302
    tenant_id = int(resp.headers["Location"].rstrip("/").split("/")[-1])
    page = client.get(f"/platform/tenants/{tenant_id}").get_data(as_text=True)
    assert "Kilimani Academy" in page and "Organisation Owner" in page and "Result slips" in page
    with app_module.app.app_context():
        ext = ussd_codes.add_code("test", "*384*7#", "extension")
    client.post(f"/platform/tenants/{tenant_id}/code", data={"csrf": csrf, "code_id": ext})
    assert "*384*7#" in client.get(f"/platform/tenants/{tenant_id}").get_data(as_text=True)
    client.post(f"/platform/tenants/{tenant_id}/setting", data={"csrf": csrf, "key": "allow_other_phone", "value": "0"})
    with app_module.app.app_context():
        assert not tenancy.allow_other_phone(tenant_id)
    assert "tenant.setting" in client.get("/platform/audit").get_data(as_text=True)


def test_reseller_sees_only_their_tenants(staff_client, make_staff):
    with app_module.app.test_request_context():
        tenancy.create_tenant("staff:root", "Someone else's", "company", "enterprise_starter")
    client, csrf = staff_client(roles=["reseller"], email="agent@oduma.co.ke")
    client.post("/platform/tenants", data={"csrf": csrf, "name": "My client", "edition": "company", "plan": "enterprise_starter"})
    page = client.get("/platform/tenants").get_data(as_text=True)
    assert "My client" in page and "Someone else" not in page
    with app_module.app.app_context():
        other = next(t for t in tenancy.tenants() if t["name"] == "Someone else's")
    assert client.get(f"/platform/tenants/{other['id']}").status_code == 404
    assert client.get("/platform/pricing").status_code == 200


def test_otp_switch_needs_sms(staff_client, fake_sms):
    client, csrf = staff_client()
    client.post("/platform/settings", data={"csrf": csrf, "action": "otp", "on": "1"})
    assert "On</span>" in client.get("/platform/settings").get_data(as_text=True)


def test_staff_cannot_lock_themselves_out(staff_client):
    client, csrf = staff_client()
    with app_module.app.app_context():
        from pb.db import fetch_one
        me = fetch_one("SELECT id FROM staff_users WHERE email = 'boss@oduma.co.ke'")["id"]
    resp = client.post(f"/platform/staff/{me}", data={"csrf": csrf, "action": "role", "role": "platform_super_admin", "on": "0"})
    assert "can" in client.get(resp.headers["Location"]).get_data(as_text=True)
    assert client.get("/platform/staff").status_code == 200


def test_admin_page_links_to_platform(client):
    page = client.get("/admin").get_data(as_text=True)
    token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    client.post("/admin/login", data={"csrf": token, "password": "admin-pass"})
    dash = client.get("/admin").get_data(as_text=True)
    assert 'href="/platform/"' in dash
    assert client.get("/platform/").status_code == 200  # same login, no second password
    assert client.get("/platform/pricing").status_code == 200
