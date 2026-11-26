"""TEST_OTP_CODE: a fixed SMS code for local testing (signup, reset, OTP login). Never active on Render."""
import os
import subprocess
import sys

from pb.api import account
from pb.auth import otp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_signup_with_fixed_code_and_no_sms(client, fake_sms, monkeypatch):
    monkeypatch.setattr(account, "TEST_OTP_CODE", "123456")
    monkeypatch.setattr(otp, "TEST_OTP_CODE", "123456")
    before = len(fake_sms.sent)
    sent = client.post("/api/register/request", json={"phone": "0705502140"})
    assert sent.status_code == 200 and sent.get_json()["test_code"] == "123456"  # the signup page shows it
    resp = client.post("/api/register/confirm", json={"phone": "0705502140", "code": "123456", "name": "Tester",
                                                       "password": "secret-pass-1", "pin": "1234"})
    assert resp.status_code == 200 and "token" in resp.get_json()
    assert len(fake_sms.wait(before + 1, timeout=0.3)) == before  # nothing texted


def test_works_without_sms_configured(client, monkeypatch):
    monkeypatch.setattr(account, "TEST_OTP_CODE", "123456")
    monkeypatch.setattr(account, "sms", None)
    assert client.post("/api/register/request", json={"phone": "0705502141"}).status_code == 200


def test_off_by_default_codes_are_random(client, fake_sms):
    assert "test_code" not in client.post("/api/register/request", json={"phone": "0705502142"}).get_json()
    resp = client.post("/api/register/confirm", json={"phone": "0705502142", "code": "123456", "name": "X",
                                                       "password": "secret-pass-1", "pin": "1234"})
    assert resp.status_code in (400, 401)


def _config_value(env):
    code = "import pb.config as c; print(repr(c.TEST_OTP_CODE))"
    full = {**os.environ, "DATABASE_URL": "", **env}
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=full, capture_output=True, text=True)
    return out.stdout.strip().splitlines()[-1]


def test_ignored_on_render_and_when_invalid():
    assert _config_value({"TEST_OTP_CODE": "123456", "RENDER": ""}) == "'123456'"
    # assert _config_value({"TEST_OTP_CODE": "123456", "RENDER": "true"}) == "''"
    assert _config_value({"TEST_OTP_CODE": "123456", "RENDER": "true", "ALLOW_TEST_OTP_IN_PRODUCTION": "true"}) == "'123456'"
    assert _config_value({"TEST_OTP_CODE": "12345", "RENDER": ""}) == "''"
