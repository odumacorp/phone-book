"""OTP: built, off by default, switched on by the Super Admin (globally) or per organisation."""
from conftest import app_module, auth
from pb.features import set_feature
from pb.rbac import engine as rbac
from pb.tenancy import tenants as tenancy


def login(client, password="secret-pass-1"):
    return client.post("/api/login", json={"login": "0712345678", "password": password})


def otp_on():
    with app_module.app.test_request_context():
        set_feature("otp", True)


def code_from(fake_sms):
    return next(m for to, m in reversed(fake_sms.wait()) if "code is" in m and "Remote Phonebook code" in m).split("code is ")[1][:6]


def test_off_by_default_login_unchanged(register, client):
    register()
    body = login(client).get_json()
    assert "token" in body and "otp_required" not in body


def test_on_needs_code(register, client, fake_sms):
    register()
    otp_on()
    body = login(client).get_json()
    assert body["otp_required"] is True and "token" not in body and body["phone_hint"] == "0712***678"
    assert client.post("/api/login/otp", json={"ticket": body["ticket"], "code": "000000"}).status_code == 401
    resp = client.post("/api/login/otp", json={"ticket": body["ticket"], "code": code_from(fake_sms)})
    assert resp.status_code == 200
    assert client.get("/api/me", headers=auth(resp.get_json()["token"])).status_code == 200
    again = client.post("/api/login/otp", json={"ticket": body["ticket"], "code": code_from(fake_sms)})
    assert again.status_code == 401  # single use


def test_wrong_password_never_sends_code(register, client, fake_sms):
    register()
    otp_on()
    before = len(fake_sms.sent)
    assert login(client, "wrong-password").status_code == 401
    assert len(fake_sms.sent) == before


def test_too_many_wrong_codes(register, client, fake_sms):
    register()
    otp_on()
    ticket = login(client).get_json()["ticket"]
    for _ in range(5):
        client.post("/api/login/otp", json={"ticket": ticket, "code": "999999"})
    resp = client.post("/api/login/otp", json={"ticket": ticket, "code": code_from(fake_sms)})
    assert resp.status_code == 401


def test_resend_rate_limited(register, client, fake_sms):
    register()
    otp_on()
    assert login(client).get_json().get("otp_required")
    assert login(client).status_code == 429


def test_organisation_can_force_otp(register, client, fake_sms):
    register()
    with app_module.app.test_request_context():
        t = tenancy.create_tenant(rbac.ROOT, "Strict SACCO", "sacco", "enterprise_standard")
        tenancy.add_member(rbac.ROOT, t, "0712345678")
        assert not tenancy.otp_on_for(t)
        tenancy.set_setting(rbac.ROOT, t, "otp", "on")
        assert tenancy.otp_on_for(t)
    assert login(client).get_json().get("otp_required")
