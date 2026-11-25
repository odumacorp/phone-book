"""Profile photos: add, change, remove (the app shows a person icon when there's none)."""
import base64

from conftest import auth

JPEG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 200).decode()
PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100).decode()


def test_add_change_and_remove_a_photo(client, register):
    token = register()
    assert client.get("/api/me", headers=auth(token)).get_json()["photo"] is None  # person icon
    resp = client.put("/api/me/photo", json={"image": JPEG}, headers=auth(token))
    assert resp.status_code == 200 and resp.get_json()["photo"] == JPEG
    assert client.get("/api/me", headers=auth(token)).get_json()["photo"] == JPEG
    client.put("/api/me/photo", json={"image": PNG}, headers=auth(token))  # change it
    assert client.get("/api/me", headers=auth(token)).get_json()["photo"] == PNG
    assert client.delete("/api/me/photo", headers=auth(token)).status_code == 200
    assert client.get("/api/me", headers=auth(token)).get_json()["photo"] is None


def test_only_real_pictures_of_a_sensible_size(client, register):
    token = register()
    bad = [
        "not a data url",
        "data:image/gif;base64,R0lGODlhAQABAAAAACw=",  # GIF isn't accepted
        "data:image/jpeg;base64," + base64.b64encode(b"<script>alert(1)</script>").decode(),  # not a JPEG
        "data:image/jpeg;base64,@@@",
        "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff" + b"\x00" * (500 * 1024)).decode(),  # too big
    ]
    for image in bad:
        resp = client.put("/api/me/photo", json={"image": image}, headers=auth(token))
        assert resp.status_code == 400, image[:40]
    assert client.get("/api/me", headers=auth(token)).get_json()["photo"] is None
    assert client.put("/api/me/photo", json={"image": JPEG}).status_code == 401  # logged in only


def test_each_person_has_their_own_photo(client, register):
    a = register()
    b = register(phone="0722000111")
    client.put("/api/me/photo", json={"image": JPEG}, headers=auth(a))
    assert client.get("/api/me", headers=auth(b)).get_json()["photo"] is None
