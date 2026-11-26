"""PostHog analytics: off without a key; anonymous IDs only; never phone numbers, names or emails."""
import json

import pytest

from conftest import auth, login_ussd
from pb import analytics


@pytest.fixture
def sent(monkeypatch):
    events = []
    monkeypatch.setattr(analytics, "POSTHOG_KEY", "phc_test")

    monkeypatch.setattr(analytics, "_dispatch", events.append)  # keep what would be sent, send nothing
    return events


def test_off_without_a_key(monkeypatch, client):
    monkeypatch.setattr(analytics, "POSTHOG_KEY", "")
    assert not analytics.enabled()
    assert client.get("/api/config").get_json()["analytics"] is None


def test_ids_are_anonymous_and_stable():
    a = analytics.person_id("+254712345678")
    assert a == analytics.person_id("+254712345678") and a.startswith("u_") and "712345678" not in a
    assert analytics.actor_id("user:+254712345678") == a
    assert analytics.actor_id("staff:3").startswith("s_")
    assert analytics.clean_path("/platform/people/254712345678") == "/platform/people/:phone"


def test_events_never_carry_personal_details(sent, client, register, dial):
    token = register(name="Asha Wanjiru", email="asha@example.com")
    client.post("/api/contacts", json={"name": "Mum", "phone": "0722000001"}, headers=auth(token))
    login_ussd(dial, "+254712345678")
    names = [e["event"] for e in sent]
    assert "signed_up" in names and "contact_added" in names and "logged_in" in names
    everything = json.dumps(sent)
    for private in ("712345678", "0722000001", "Asha", "Wanjiru", "asha@example.com", "Mum"):
        assert private not in everything, private
    me = client.get("/api/me", headers=auth(token)).get_json()
    assert me["analytics_id"] == analytics.person_id("+254712345678")
    assert client.get("/api/config").get_json()["analytics"]["key"] == "phc_test"
