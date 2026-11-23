"""Today's app API, website and admin behaviour, pinned down before the code is reorganised."""
import re

from conftest import app_module, auth


def test_health_and_website(client):
    assert client.get("/health").status_code == 200
    resp = client.get("/")
    assert resp.status_code == 200 and b"<html" in resp.data.lower()


def test_register_login_me(register, client):
    register(email="asha@example.com")
    resp = client.post("/api/login", json={"login": "0712345678", "password": "secret-pass-1"})
    assert resp.status_code == 200
    token = resp.get_json()["token"]
    me = client.get("/api/me", headers=auth(token)).get_json()
    assert me["phone"] == "+254712345678" and me["name"] == "Asha" and me["email"] == "asha@example.com"
    assert client.post("/api/login", json={"login": "asha@example.com", "password": "secret-pass-1"}).status_code == 200
    assert client.post("/api/login", json={"login": "0712345678", "password": "wrong-pass"}).status_code == 401


def test_register_twice_refused(register, client):
    register()
    resp = client.post("/api/register/request", json={"phone": "0712345678"})
    assert resp.status_code == 409


def test_auth_required(client):
    assert client.get("/api/contacts").status_code == 401
    assert client.get("/api/contacts", headers=auth("garbage")).status_code == 401


def test_contacts_crud_and_encryption(register, client):
    token = register()
    resp = client.post("/api/contacts", json={"name": "Juma", "phone": "0733111222"}, headers=auth(token))
    assert resp.status_code == 201
    cid = resp.get_json()["id"]
    assert client.post("/api/contacts", json={"name": "J2", "phone": "0733111222"}, headers=auth(token)).status_code == 409
    resp = client.patch(f"/api/contacts/{cid}", json={"name": "Juma K", "phone": "0733111222"}, headers=auth(token))
    assert resp.status_code == 200
    names = [c["name"] for c in client.get("/api/contacts", headers=auth(token)).get_json()["contacts"]]
    assert names == ["Juma K"]
    assert client.delete(f"/api/contacts/{cid}", headers=auth(token)).status_code == 200
    assert client.get("/api/contacts", headers=auth(token)).get_json()["contacts"] == []


def test_contacts_are_private_per_account(register, client):
    a = register(phone="0712345678")
    b = register(phone="0798765432")
    cid = client.post("/api/contacts", json={"name": "Juma", "phone": "0733111222"}, headers=auth(a)).get_json()["id"]
    assert client.get("/api/contacts", headers=auth(b)).get_json()["contacts"] == []
    client.delete(f"/api/contacts/{cid}", headers=auth(b))
    assert len(client.get("/api/contacts", headers=auth(a)).get_json()["contacts"]) == 1


def test_sync_dedupes(register, client):
    token = register()
    resp = client.post("/api/contacts/sync", headers=auth(token), json={"contacts": [
        {"name": "A", "phone": "0711000001"},
        {"name": "A again", "phone": "+254711000001"},
        {"name": "", "phone": "0711000002"},
        {"name": "B", "phone": "0711000003"},
    ]})
    assert resp.get_json() == {"added": 2, "skipped": 2, "total": 2}


def test_logout_revokes_token(register, client):
    token = register()
    assert client.post("/api/logout", json={}, headers=auth(token)).status_code == 200
    assert client.get("/api/me", headers=auth(token)).status_code == 401


def test_change_pin_then_ussd(register, client, dial):
    token = register()
    resp = client.post("/api/pin/change", headers=auth(token), json={
        "password": "secret-pass-1", "new_pin": "4321", "confirm_pin": "4321",
    })
    assert resp.status_code == 200, resp.get_json()
    s = dial("+254712345678")
    s.send("0712345678")
    assert s.send("4321").startswith("CON Remote Phone-Book")


def test_admin_login_and_feature_switch(client):
    page = client.get("/admin").get_data(as_text=True)
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    assert client.post("/admin/login", data={"csrf": csrf, "password": "nope"}).status_code == 302
    assert "Wrong password" in client.get("/admin?error=Wrong+password.").get_data(as_text=True)
    client.post("/admin/login", data={"csrf": csrf, "password": "admin-pass"})
    dash = client.get("/admin").get_data(as_text=True)
    csrf = re.search(r'name="csrf" value="([^"]+)"', dash).group(1)  # a new token after logging in
    assert "Feature" in dash or "feature" in dash
    assert client.post("/admin/feature", data={"name": "encrypt_contacts", "on": "1"}).status_code == 403  # no CSRF
    resp = client.post("/admin/feature", data={"csrf": csrf, "name": "encrypt_contacts", "on": "1"})
    assert resp.status_code == 302


def admin_login(client):
    page = client.get("/admin").get_data(as_text=True)
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    client.post("/admin/login", data={"csrf": csrf, "password": "admin-pass"})
    return re.search(r'name="csrf" value="([^"]+)"', client.get("/admin").get_data(as_text=True)).group(1)


def test_encrypted_contacts_still_work_on_ussd(register, client, dial):
    token = register()
    client.post("/api/contacts", json={"name": "Juma", "phone": "0733111222"}, headers=auth(token))
    csrf = admin_login(client)
    resp = client.post("/admin/feature", data={"csrf": csrf, "name": "encrypt_contacts", "on": "1"})
    assert resp.status_code == 302
    from pb.db import fetch_one
    with app_module.app.app_context():
        row = fetch_one("SELECT name, enc FROM contacts")
    assert row["enc"] == 1 and row["name"] != "Juma"  # stored as ciphertext
    s = dial("+254712345678")
    s.send("0712345678")
    s.send("1234")
    assert "Juma" in s.send("1")
