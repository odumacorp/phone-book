"""The organisation dashboard: the service layer (real and mock), date-range filtering, admin vs viewer, and what's
recorded for it (USSD outcomes, lookups, deliveries, delivery reports)."""
import datetime
import time

import pytest

from conftest import app_module, auth, login_ussd
from pb import stats
from pb.dashboard import service, track
from pb.db import execute, fetch_all, fetch_one
from test_org_content import ADMIN, CLIENT, MEMBER, portal_login, setup_org


# ---------------------------------------------------------------- date ranges

def test_date_ranges_are_whole_days_in_nairobi_time():
    now = int(datetime.datetime(2026, 11, 26, 9, 30, tzinfo=stats.EAT).timestamp())
    key, _label, start, end = stats.period("7d", now=now)
    assert key == "7d" and end - start == 7 * 86400
    assert datetime.datetime.fromtimestamp(start, stats.EAT).strftime("%Y-%m-%d %H:%M") == "2026-11-20 00:00"
    assert datetime.datetime.fromtimestamp(end, stats.EAT).strftime("%Y-%m-%d %H:%M") == "2026-11-27 00:00"
    _k, _l, a, b = stats.period("custom", "2026-11-01", "2026-11-15")
    assert (b - a) == 15 * 86400  # both days included
    assert stats.period("custom", "not-a-date", "x")[0] == "30d"  # bad dates fall back


def test_cards_only_count_inside_the_range(client, register):
    t = setup_org(register)
    now = int(time.time())
    with app_module.app.test_request_context():
        for days_ago in (1, 2, 20):  # two USSD sessions inside "last 7 days", one outside
            execute("INSERT INTO ussd_sessions (owner, caller, started_at, last_active, reached_contact) VALUES (?, ?, ?, ?, ?)",
                    ("+254712345678", "+254712345678", now - days_ago * 86400, now - days_ago * 86400, 1 if days_ago == 1 else 0))
        week = service.card("ussd_sessions", t, "7d")
        month = service.card("ussd_sessions", t, "30d")
        drop = service.card("dropoff", t, "7d")
    assert sum(week["series"][0]["values"]) == 2 and sum(month["series"][0]["values"]) == 3
    assert [v for v in drop["series"][0]["values"] if v is not None] == [0.0, 100.0] or sorted(
        v for v in drop["series"][0]["values"] if v is not None) == [0.0, 100.0]


# ---------------------------------------------------------------- service layer

@pytest.mark.parametrize("mode", ["real", "mock"])
def test_every_card_has_its_shape(monkeypatch, client, register, mode):
    monkeypatch.setenv("DASHBOARD_DATA", mode)
    t = setup_org(register)
    with app_module.app.test_request_context():
        for key, _title, _admin in service.CARDS:
            card = service.card(key, t, "7d")
            assert card["key"] == key and card["kind"] in ("line", "kpis", "donut", "table", "billing")
            assert card["source"] == ("demo" if mode == "mock" else "live")
            if card["kind"] == "line":
                assert all(len(s["values"]) == len(card["labels"]) for s in card["series"])
    if mode == "mock":
        with app_module.app.test_request_context():
            assert not service.card("delivery", t, "7d")["empty"]
            failures = service.card("failures", t)
            assert len(failures["labels"]) == 96 and len(failures["data"]["per_minute"]["Past 24 hours"]) == 1440


def test_viewers_never_get_billing(client, register):
    t = setup_org(register)
    assert "billing" not in dict(service.cards_for(False)) and "billing" in dict(service.cards_for(True))
    with app_module.app.test_request_context(), pytest.raises(PermissionError):
        service.card("billing", t, is_admin=False)
    with app_module.app.test_request_context(), pytest.raises(KeyError):
        service.card("nope", t)


def test_mock_data_is_kenyan_and_dips_at_weekends(monkeypatch, client, register):
    monkeypatch.setenv("DASHBOARD_DATA", "mock")
    t = setup_org(register)
    with app_module.app.test_request_context():
        card = service.card("ussd_sessions", t, "30d")
        bill = service.card("billing", t)
    days = stats.buckets(*stats.period("30d")[2:])
    weekday = [v for (_l, a, _b), v in zip(days, card["series"][0]["values"]) if datetime.datetime.fromtimestamp(a, stats.EAT).weekday() < 5]
    sunday = [v for (_l, a, _b), v in zip(days, card["series"][0]["values"]) if datetime.datetime.fromtimestamp(a, stats.EAT).weekday() == 6]
    assert sum(sunday) / len(sunday) < sum(weekday) / len(weekday)
    assert bill["data"]["amount_cents"] > 0 and bill["data"]["last_payment"]["mpesa_ref"]


# ---------------------------------------------------------------- the page and its permissions

def test_admin_sees_everything_viewer_sees_charts(client, register):
    t = setup_org(register)
    _r, _csrf = portal_login(client, ADMIN)
    page = client.get(f"/platform/org/{t}/panel").get_data(as_text=True)
    assert "<h1>Dashboard</h1>" in page and "+ New dashboard" in page and '"billing"' in page and "Add filter" in page
    resp = client.get(f"/platform/org/{t}/panel/card/members?range=7d")
    assert resp.status_code == 200 and resp.get_json()["kind"] == "kpis"
    assert client.get(f"/platform/org/{t}/panel/card/billing").status_code == 200
    assert client.get(f"/platform/org/{t}/panel/card/nope").status_code == 404
    with app_module.app.test_request_context():
        from pb.rbac import engine as rbac
        rbac.grant(rbac.ROOT, t, rbac.user_principal("+254713000003"), "auditor", as_platform=True)
        for a in rbac.tenant_assignments(t):
            if a["principal"] == rbac.user_principal("+254713000003") and a["key"] == "client":
                rbac.revoke(rbac.ROOT, t, a["id"], as_platform=True)
    viewer = client.application.test_client()
    portal_login(viewer, CLIENT)
    page = viewer.get(f"/platform/org/{t}/panel").get_data(as_text=True)
    assert "<h1>Dashboard</h1>" in page and "+ New dashboard" not in page and '"billing"' not in page
    assert "Needs your attention" not in page
    assert viewer.get(f"/platform/org/{t}/panel/card/billing").status_code == 403
    assert viewer.get(f"/platform/org/{t}/panel/card/ussd_sessions").status_code == 200


# ---------------------------------------------------------------- what's recorded

def test_ussd_lookups_and_reaching_a_contact_are_recorded(client, register, dial):
    token = register()
    client.post("/api/contacts", json={"name": "Mum", "phone": "0722000001"}, headers=auth(token))
    s = login_ussd(dial, "+254712345678")
    s.send("1")  # My contacts
    with app_module.app.test_request_context():
        assert fetch_one("SELECT channel FROM lookups WHERE owner = ?", ("+254712345678",))["channel"] == "ussd"
        assert fetch_one("SELECT reached_contact FROM ussd_sessions WHERE owner = ? ORDER BY id DESC", ("+254712345678",))["reached_contact"] == 1
    client.get("/api/contacts", headers={**auth(token), "X-Platform": "android"})
    with app_module.app.test_request_context():
        assert {r["channel"] for r in fetch_all("SELECT channel FROM lookups")} == {"ussd", "android"}


def test_notice_delivery_and_views(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/notices", data={"csrf": csrf, "title": "Fees", "body": "Due Friday."})
    with app_module.app.test_request_context():
        rows = fetch_all("SELECT owner, delivered_at, viewed_at FROM deliveries WHERE tenant_id = ? AND kind = 'notice'", (t,))
    assert len(rows) >= 3 and all(r["delivered_at"] for r in rows) and not any(r["viewed_at"] for r in rows)
    member = client.post("/api/login", json={"login": MEMBER, "password": "secret-pass-1"}).get_json()["token"]
    client.get(f"/api/orgs/{t}", headers=auth(member))
    with app_module.app.test_request_context():
        card = service.card("delivery", t, "7d")
    sent, delivered, viewed = (sum(s["values"]) for s in card["series"])
    assert sent == delivered >= 3 and viewed == 1


def test_sms_delivery_reports(client, register):
    t = setup_org(register)
    with app_module.app.test_request_context():
        track.sent(t, "reminder", 1, ["+254712345678"], "sms", provider_ids={"+254712345678": "ATXid_1"})
        token = track.delivery_report_token()
    assert client.post("/sms/delivery/wrong", data={"id": "ATXid_1", "status": "Success"}).status_code == 404
    assert client.post(f"/sms/delivery/{token}", data={"id": "ATXid_1", "status": "Success"}).status_code == 200
    with app_module.app.test_request_context():
        row = fetch_one("SELECT status, delivered_at FROM deliveries WHERE provider_id = 'ATXid_1'")
    assert row["status"] == "delivered" and row["delivered_at"]


def test_failures_per_minute(client, register):
    t = setup_org(register)
    now = int(time.time())
    with app_module.app.test_request_context():
        track.ussd_failure("+254712345678", "timeout")
        execute("INSERT INTO ussd_failures (caller, kind, created_at) VALUES (?, 'error', ?)", ("+254712345678", now - 7 * 86400))
        card = service.card("failures", t)
    today, last_week = (sum(v) for v in card["data"]["per_minute"].values())
    assert today == 1 and last_week == 1 and len(card["labels"]) == 96


# ---------------------------------------------------------------- the platform dashboard (Oduma Corp staff)

def test_super_admin_platform_dashboard(client, register, staff_client):
    t = setup_org(register)
    sc, csrf = staff_client()
    page = sc.get("/platform/panel").get_data(as_text=True)
    assert "<h1>Dashboard</h1>" in page and "+ New dashboard" in page and 'id="f-org"' in page and "Test Advocates" in page
    assert '"organisations"' in page and '"billing"' in page and "Organisations to check" in page
    for key in ("organisations", "members", "ussd_sessions", "dropoff", "delivery", "failures", "channel_mix", "top_groups", "billing"):
        resp = sc.get(f"/platform/panel/card/{key}?range=30d")
        assert resp.status_code == 200, key
    revenue = sc.get("/platform/panel/card/billing").get_json()
    assert revenue["kind"] == "revenue" and revenue["title"] == "Revenue"
    top = sc.get("/platform/panel/card/top_groups?range=all").get_json()
    assert top["title"] == "Top organisations" and any(r["name"] == "Test Advocates" for r in top["data"]["rows"])
    one = sc.get(f"/platform/panel/card/billing?tenant={t}").get_json()  # filtered to one organisation: its own billing
    assert one["kind"] == "billing"
    people = sc.get("/platform/panel/card/members?range=all").get_json()
    assert people["title"] == "People" and people["data"]["tiles"][0]["value"] >= 4
    assert sc.get(f"/platform/org/{t}/panel").status_code == 200  # super admins open any organisation's dashboard


def test_support_staff_view_only_and_no_revenue(client, register, staff_client):
    setup_org(register)
    sc, _ = staff_client(roles=("platform_support",), email="support@oduma.co.ke")
    page = sc.get("/platform/panel").get_data(as_text=True)
    assert "<h1>Dashboard</h1>" in page and "+ New dashboard" not in page and '"billing"' not in page
    assert sc.get("/platform/panel/card/billing").status_code == 403
    assert sc.get("/platform/panel/card/organisations").status_code == 200
    fin, _ = staff_client(roles=("platform_finance",), email="money@oduma.co.ke")
    assert fin.get("/platform/panel/card/billing").status_code == 200  # finance staff see revenue


@pytest.mark.parametrize("mode", ["real", "mock"])
def test_platform_cards_have_their_shape(monkeypatch, client, register, mode):
    monkeypatch.setenv("DASHBOARD_DATA", mode)
    setup_org(register)
    with app_module.app.test_request_context():
        for key, _title, _who in service.PLATFORM_CARDS:
            card = service.card(key, None, "7d", scope="platform", is_admin=True)
            assert card["key"] == key
