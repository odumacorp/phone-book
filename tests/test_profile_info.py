"""The profile shows the person's organisations (role, title, departments) and their previous login."""
from conftest import app_module, auth, login_ussd
from pb.rbac import engine as rbac
from pb.tenancy import tenants as tenancy
from pb.tenancy.repo import TenantRepo
from test_org_content import ADMIN, MEMBER, portal_login, setup_org


def me(client, token):
    return client.get("/api/me", headers=auth(token)).get_json()


def test_memberships_on_profile(client, register):
    t = setup_org(register)
    with app_module.app.test_request_context():
        repo = TenantRepo(t)
        dept = repo.insert("departments", name="Sales", created_at=0)
        member = repo.one("memberships", "user_phone = ?", ("+254712345678",))
        repo.update("memberships", "id = ?", (member["id"],), title="Driver", member_no="EMP-9")
        repo.insert("department_members", returning_id=False, department_id=dept, membership_id=member["id"])
    token = client.post("/api/login", json={"login": MEMBER, "password": "secret-pass-1"}).get_json()["token"]
    m = me(client, token)["memberships"]
    assert m == [{"id": t, "name": "Test Advocates", "roles": ["Member"], "title": "Driver", "member_no": "EMP-9",
                  "groups": ["Sales"], "joined_at": m[0]["joined_at"]}]


def test_individual_has_no_memberships(client, register):
    token = register(phone="0700100001")
    assert me(client, token)["memberships"] == []


def test_last_login_is_the_previous_one(client, register, dial):
    token = register()  # registering logs in (that's the first app session)
    assert me(client, token)["last_login"] is None  # no earlier login
    second = client.post("/api/login", json={"login": "0712345678", "password": "secret-pass-1"},
                         headers={"X-Device-Name": "Chrome on Mac (website)"}).get_json()["token"]
    info = me(client, second)["last_login"]
    assert info["how"] == "app"  # the registration login
    login_ussd(dial, "+254799000000")  # a USSD login from a borrowed phone
    info = me(client, second)["last_login"]
    assert info["how"] == "USSD" and info["where"].startswith("0799000000")


def test_members_list_shows_last_active(client, register):
    t = setup_org(register)
    portal_login(client, ADMIN)
    page = client.get(f"/platform/org/{t}/members").get_data(as_text=True)
    assert "Last active" in page and "app" in page  # everyone in setup_org has logged in via registration
    with app_module.app.test_request_context():
        tenancy.add_member(rbac.ROOT, t, "0716000006")  # never logged in
    assert "Never" in client.get(f"/platform/org/{t}/members").get_data(as_text=True)
