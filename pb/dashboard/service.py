"""dashboard_service: the one place the organisation dashboard gets its data.

DASHBOARD_DATA=real (default) reads what Remote Phonebook records; DASHBOARD_DATA=mock returns demo data for every card.
To plug in another source later (e.g. an external API), add a provider module with the same CARDS functions and choose it
in _provider().
"""
import os

from pb import stats
from pb.dashboard import mock, real

PLATFORM_CARDS = [  # Oduma Corp staff: key, title, who may see it ("all", "admin": super admins, "finance": money)
    ("organisations", "Organisations", "all"),
    ("members", "People", "all"),
    ("ussd_sessions", "USSD sessions", "all"),
    ("dropoff", "Session drop-off rate", "all"),
    ("delivery", "Notice delivery", "all"),
    ("failures", "Failed sessions in real time", "all"),
    ("channel_mix", "Channel mix", "all"),
    ("top_groups", "Top organisations", "all"),
    ("billing", "Revenue", "finance"),
]
CARDS = [  # an organisation's admins: key, title, admin only (viewers never get it)
    ("ussd_sessions", "USSD sessions", False),
    ("dropoff", "Session drop-off rate", False),
    ("delivery", "Notice delivery", False),
    ("failures", "Failed sessions in real time", False),
    ("members", "Members", False),
    ("channel_mix", "Channel mix", False),
    ("top_groups", "Top groups", False),
    ("billing", "Billing", True),
]
DEFAULT_RANGE = "7d"
CHANNELS = {"ussd": "USSD", "android": "Android", "ios": "iPhone", "web": "Web"}


def _provider():
    return mock if os.getenv("DASHBOARD_DATA", "real").strip().lower() == "mock" else real


def cards_for(is_admin, scope="org", can_finance=False):
    """The cards this person may see. Organisations: viewers get everything except Billing. Platform: Revenue only for
    super admins and finance staff."""
    if scope == "platform":
        return [(key, title) for key, title, who in PLATFORM_CARDS if who == "all" or (who == "finance" and (can_finance or is_admin))]
    return [(key, title) for key, title, admin_only in CARDS if is_admin or not admin_only]


def card(key, tenant_id, range_key=DEFAULT_RANGE, start=None, end=None, group=None, channel=None, is_admin=True,
         scope="org", can_finance=False):
    """One card's data as a dict for the page. Raises KeyError for an unknown card and PermissionError for one this person
    may not see. Filters: group ("d:<id>" / "g:<id>"), channel (ussd/android/ios/web).
    scope "platform": tenant_id None counts every organisation; a tenant_id narrows it to that organisation."""
    allowed = dict(cards_for(is_admin, scope, can_finance))
    known = PLATFORM_CARDS if scope == "platform" else CARDS
    if key not in dict((k, t) for k, t, _a in known):
        raise KeyError(key)
    if key not in allowed:
        raise PermissionError(key)
    range_key, label, start, end = stats.period(range_key or DEFAULT_RANGE, start, end)
    channel = channel if channel in CHANNELS else None
    group = group if group and group[:2] in ("d:", "g:") and group[2:].isdigit() else None
    subtitle = label if label.startswith(("Last", "This", "All")) or range_key == "custom" else label
    subtitle = {"7d": "Past 7 days", "30d": "Past 30 days", "today": "Today", "yesterday": "Yesterday"}.get(range_key, subtitle)
    result = _provider().CARDS[key](tenant_id, start, end, group=group, channel=channel, label=subtitle)
    return result.to_json()
