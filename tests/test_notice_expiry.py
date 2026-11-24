"""Notices: "Show it for" list (1 day ... 1 year, until deleted) and a calendar date."""
import datetime
import re

import pytest

from conftest import app_module
from pb.org import content
from pb.tenancy.repo import TenantRepo
from test_org_content import ADMIN, portal_login, setup_org

NOW = 1_800_000_000  # a fixed moment for the pure-function tests


def test_expiry_choices():
    assert content.notice_expiry({"expires_mode": ""}, NOW) is None
    assert content.notice_expiry({"expires_mode": "7"}, NOW) == NOW + 7 * 86400
    assert content.notice_expiry({"expires_days": "30"}, NOW) == NOW + 30 * 86400  # older form field still works
    day = datetime.datetime.fromtimestamp(NOW, content.EAT).date() + datetime.timedelta(days=10)
    end = content.notice_expiry({"expires_mode": "date", "expires_date": day.isoformat()}, NOW)
    as_eat = datetime.datetime.fromtimestamp(end, content.EAT)
    assert as_eat.date() == day and (as_eat.hour, as_eat.minute) == (23, 59)  # end of that day, Kenya time
    for bad in ({"expires_mode": "date", "expires_date": ""}, {"expires_mode": "date", "expires_date": "2020-01-01"},
                {"expires_mode": "date", "expires_date": "31/12/2030"}, {"expires_mode": "9999"}, {"expires_mode": "abc"},
                {"expires_mode": "date", "expires_date": (day + datetime.timedelta(days=900)).isoformat()}):
        with pytest.raises(ValueError):
            content.notice_expiry(bad, NOW)


def test_form_offers_list_and_calendar(client, register):
    t = setup_org(register)
    portal_login(client, ADMIN)
    page = client.get(f"/platform/org/{t}/notices").get_data(as_text=True)
    assert '<select name="expires_mode"' in page and ">1 week</option>" in page and ">Pick a date…</option>" in page
    assert 'type="date" name="expires_date"' in page and ">Until I delete it</option>" in page


def test_post_with_date_and_edit_keeps_it(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    day = (datetime.datetime.now(content.EAT).date() + datetime.timedelta(days=5)).isoformat()
    client.post(f"/platform/org/{t}/notices", data={"csrf": csrf, "title": "Sports day", "body": "Friday", "expires_mode": "date",
                                                     "expires_date": day})
    page = client.get(f"/platform/org/{t}/notices").get_data(as_text=True)
    notice_id = re.search(r"\?edit=(\d+)", page).group(1)
    edit = client.get(f"/platform/org/{t}/notices?edit={notice_id}").get_data(as_text=True)
    assert f'value="{day}"' in edit and '<option value="date" selected>' in edit
    resp = client.post(f"/platform/org/{t}/notices", data={"csrf": csrf, "title": "Sports day", "body": "x", "expires_mode": "date",
                                                            "expires_date": "2001-01-01"})
    assert "past" in client.get(resp.headers["Location"]).get_data(as_text=True)


def test_expired_notices_disappear_for_members(client, register):
    t = setup_org(register)
    _r, csrf = portal_login(client, ADMIN)
    client.post(f"/platform/org/{t}/notices", data={"csrf": csrf, "title": "Short one", "body": "x", "expires_mode": "1"})
    with app_module.app.test_request_context():
        assert [n["title"] for n in content.visible_notices(t, "+254712345678")] == ["Short one"]
        TenantRepo(t).update("notices", "title = ?", ("Short one",), expires_at=1)  # time passes
        assert content.visible_notices(t, "+254712345678") == []
    assert "Expired" in client.get(f"/platform/org/{t}/notices").get_data(as_text=True)  # still listed for the operator
