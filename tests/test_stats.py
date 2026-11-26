"""The Admin panel: staff see and run the whole platform; an organisation's admins see and run only their organisation."""
from conftest import app_module
from pb import stats
from test_org_content import ADMIN, CLIENT, portal_login, setup_org


def test_periods_and_changes():
    now = 1_800_000_000
    key, _label, a, b = stats.period("7d", now=now)
    assert key == "7d" and b - a == 7 * 86400
    assert stats.period("custom", "2026-11-10", "2026-11-01")[2] < stats.period("custom", "2026-11-10", "2026-11-01")[3]
    assert stats.period("nonsense")[0] == "30d"
    assert stats.change(15, 10) == {"value": 15, "before": 10, "pct": 50, "dir": "up"}
    assert stats.change(3, 0)["pct"] is None
    assert len(stats.buckets(*stats.period("30d", now=now)[2:])) == 30
    assert len(stats.buckets(*stats.period("6m", now=now)[2:])) < 30  # weeks, not days


def test_staff_dashboard(client, register, staff_client):
    setup_org(register)
    sc, _ = staff_client()
    for r in list(stats.RANGES) + ["custom&from=2026-01-01&to=2026-12-31"]:
        page = sc.get(f"/platform/panel?range={r}").get_data(as_text=True)
        assert "<h1>Dashboard</h1>" in page and "Newest accounts" in page and 'id="dash-grid"' in page
    page = sc.get("/platform/panel?range=all").get_data(as_text=True)
    assert "Test Advocates" in page  # in the organisation filter
    assert '/platform/panel"' in sc.get("/platform/").get_data(as_text=True)  # the button on Overview
    assert sc.get("/platform/stats").headers["Location"].endswith("/platform/panel")  # old links still work


def test_org_dashboard_is_limited_to_the_organisation(client, register, staff_client):
    t = setup_org(register)
    with app_module.app.test_request_context():
        from pb.rbac import engine as rbac
        from pb.tenancy import tenants as tenancy
        other = tenancy.create_tenant(rbac.ROOT, "Other Org", "company", "enterprise_standard")
    _r, _csrf = portal_login(client, ADMIN)
    page = client.get(f"/platform/org/{t}/panel?range=all").get_data(as_text=True)
    assert "<h1>Dashboard</h1>" in page and "Other Org" not in page  # the organisation's own dashboard
    assert "New accounts" not in page and "Largest organisations" not in page  # platform-wide figures stay with staff
    assert f'/platform/org/{t}/panel"' in client.get(f"/platform/org/{t}").get_data(as_text=True)  # the button on Home
    assert client.get(f"/platform/org/{other}/panel").status_code == 404
    assert client.get("/platform/panel").status_code in (302, 403)
    client2 = client.application.test_client()
    resp, _ = portal_login(client2, CLIENT)
    assert client2.get(f"/platform/org/{t}/panel").status_code in (302, 403, 404)


def test_org_admin_acts_from_the_panel(client, register):
    from conftest import auth
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    page = client.get(f"/platform/org/{t}/panel").get_data(as_text=True)
    assert "Post a quick notice" in page and "Requests to join" in page and 'id="dash-grid"' in page
    here = f"/platform/org/{t}/panel"
    resp = client.post(f"/platform/org/{t}/notices", data={"csrf": csrf, "title": "Office closed", "body": "Closed on Friday.",
                                                            "next": here})
    assert resp.headers["Location"].endswith(here)  # back on the panel
    member = client.post("/api/login", json={"login": CLIENT, "password": "secret-pass-1"}).get_json()["token"]
    notices = client.get(f"/api/orgs/{t}", headers=auth(member)).get_json()["notices"]
    assert any(n["title"] == "Office closed" for n in notices)
    resp = client.post(f"/platform/org/{t}/notices", data={"csrf": csrf, "title": "X", "body": "Y", "next": "https://evil.example/"})
    assert "evil" not in resp.headers["Location"]  # only pages on the platform


def test_staff_act_from_the_panel(client, register, staff_client):
    from pb.db import fetch_one
    t = setup_org(register)
    sc, csrf = staff_client()
    with app_module.app.test_request_context():
        from pb.rbac import engine as rbac
        from pb.tenancy import tenants as tenancy
        tenancy.update_tenant(rbac.ROOT, t, status="trial")
    page = sc.get("/platform/panel").get_data(as_text=True)
    assert "Organisations to check" in page and "Newest accounts" in page
    resp = sc.post(f"/platform/tenants/{t}/update", data={"csrf": csrf, "status": "active", "next": "/platform/panel"})
    assert resp.headers["Location"].endswith("/platform/panel")
    with app_module.app.test_request_context():
        assert fetch_one("SELECT status FROM tenants WHERE id = ?", (t,))["status"] == "active"
    sc.post("/platform/people/254712345678", data={"csrf": csrf, "action": "off", "next": "/platform/panel"})
    with app_module.app.test_request_context():
        assert fetch_one("SELECT disabled FROM users WHERE phone = ?", ("+254712345678",))["disabled"] == 1


def test_read_only_staff_see_no_buttons(client, register, staff_client):
    setup_org(register)
    sc, _ = staff_client(roles=("platform_support",), email="support@oduma.co.ke")
    page = sc.get("/platform/panel").get_data(as_text=True)
    assert "<h1>Dashboard</h1>" in page and 'value="off"' not in page and 'action="/platform/tenants/' not in page
