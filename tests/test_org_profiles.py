"""Organisation profiles: admins set details and a logo; members see them in the app; the platform shows them."""
import base64
import io

from conftest import auth
from test_org_content import ADMIN, MEMBER, portal_login, setup_org

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def member_token(client):
    return client.post("/api/login", json={"login": MEMBER, "password": "secret-pass-1"}).get_json()["token"]


def test_admin_sets_profile_and_logo_members_see_it(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    resp = client.post(f"/platform/org/{t}/profile", data={
        "csrf": csrf, "about": "Advocates since 1998.", "phone": "020 123 4567", "email": "info@advocates.example",
        "website": "www.advocates.example", "address": "Upper Hill, Nairobi",
        "logo": (io.BytesIO(PNG), "logo.png", "image/png")}, content_type="multipart/form-data")
    assert resp.status_code == 302
    page = client.get(f"/platform/org/{t}/settings").get_data(as_text=True)
    assert "Advocates since 1998." in page and f"/logos/{t}.png" in page and 'class="org-logo"' in page  # sidebar too
    token = member_token(client)
    org = client.get(f"/api/orgs/{t}", headers=auth(token)).get_json()["org"]
    assert org["about"] == "Advocates since 1998." and org["website"] == "https://www.advocates.example"
    assert org["logo_url"].startswith(f"/logos/{t}.png?v=")
    assert client.get("/api/orgs", headers=auth(token)).get_json()["orgs"][0]["logo_url"] == org["logo_url"]
    logo = client.get(org["logo_url"])
    assert logo.status_code == 200 and logo.data == PNG and logo.mimetype == "image/png"
    assert "immutable" in logo.headers["Cache-Control"]
    # Remove the logo: the app shows a building icon
    client.post(f"/platform/org/{t}/profile", data={"csrf": csrf, "action": "remove_logo"})
    assert client.get(f"/api/orgs/{t}", headers=auth(token)).get_json()["org"]["logo_url"] is None
    assert client.get(f"/logos/{t}.png").status_code == 404


def test_bad_logos_and_details_are_refused(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    for data in ({"logo": (io.BytesIO(svg), "logo.svg", "image/svg+xml")},
                 {"logo": (io.BytesIO(b"hello"), "logo.png", "image/png")},
                 {"email": "not-an-email"}, {"website": "javascript:alert(1)"}):
        client.post(f"/platform/org/{t}/profile", data={"csrf": csrf, **data}, content_type="multipart/form-data")
        page = client.get(f"/platform/org/{t}/settings").get_data(as_text=True)
        assert f"/logos/{t}." not in page and "javascript:" not in page.split("Organisation profile")[1][:4000]


def test_members_cant_change_it_and_staff_see_it(client, register, staff_client):
    t = setup_org(register)
    _r, csrf = portal_login(client, MEMBER)
    resp = client.post(f"/platform/org/{t}/profile", data={"csrf": csrf or "x", "about": "Hacked"})
    assert resp.status_code in (302, 403, 404)  # members don't get the workspace at all
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/profile", data={"csrf": csrf, "about": "Advocates since 1998."})
    sc, _ = staff_client()
    assert "Advocates since 1998." in sc.get(f"/platform/tenants/{t}").get_data(as_text=True)
    assert "Hacked" not in sc.get(f"/platform/tenants/{t}").get_data(as_text=True)
