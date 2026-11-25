"""The platform's People pages: staff see individuals and organisation members and manage their accounts."""
from conftest import app_module, auth
from test_org_content import MEMBER, setup_org


def api_login(client, login, password="secret-pass-1"):
    return client.post("/api/login", json={"login": login, "password": password})


def test_list_search_and_filter(client, register, staff_client):
    setup_org(register)  # registers MEMBER and others into an organisation
    register(phone="0799000111", name="Solo Individual")
    sc, _ = staff_client()
    page = sc.get("/platform/people").get_data(as_text=True)
    assert "Solo Individual" in page and "Individual" in page and "Organisation member" in page
    only = sc.get("/platform/people?kind=individual").get_data(as_text=True)
    assert "Solo Individual" in only and "Organisation member" not in only
    found = sc.get("/platform/people?q=0799000111").get_data(as_text=True)
    assert "Solo Individual" in found and "1 person" in found


def test_manage_an_account(client, register, staff_client):
    token = register(phone="0799000111", name="Solo Individual", password="secret-pass-1")
    sc, csrf = staff_client()
    page = sc.get("/platform/people/254799000111").get_data(as_text=True)
    assert "Solo Individual" in page and "private: not shown" in page and "an individual" in page
    other = client.application.test_client()
    sc.post("/platform/people/254799000111", data={"csrf": csrf, "action": "logout"})
    assert other.get("/api/contacts", headers=auth(token)).status_code == 401
    sc.post("/platform/people/254799000111", data={"csrf": csrf, "action": "off"})
    assert api_login(other, "0799000111").status_code == 401
    sc.post("/platform/people/254799000111", data={"csrf": csrf, "action": "on"})
    assert api_login(other, "0799000111").status_code == 200
    sc.post("/platform/people/254799000111", data={"csrf": csrf, "action": "rename", "name": "Solo Renamed"})
    assert "Solo Renamed" in sc.get("/platform/people/254799000111").get_data(as_text=True)
    for _ in range(3):
        api_login(other, "0799000111", "wrong-password")
    assert "Unlock" in sc.get("/platform/people/254799000111").get_data(as_text=True)
    sc.post("/platform/people/254799000111", data={"csrf": csrf, "action": "unlock"})
    assert "wrong PIN/password attempts" not in sc.get("/platform/people/254799000111").get_data(as_text=True)
    assert sc.get("/platform/people/254700000000").status_code == 404


def test_support_can_view_but_not_manage(client, register, staff_client):
    register(phone="0799000111", name="Solo Individual")
    sc, csrf = staff_client(roles=("platform_support",), email="support@oduma.co.ke")
    page = sc.get("/platform/people/254799000111").get_data(as_text=True)
    assert "Solo Individual" in page and "Log out everywhere" not in page
    assert sc.post("/platform/people/254799000111", data={"csrf": csrf, "action": "off"}).status_code == 403
    sc2, _ = staff_client(roles=("platform_finance",), email="money@oduma.co.ke")
    assert sc2.get("/platform/people").status_code == 403
