"""Typed models behind the organisation dashboard (what each provider returns, real or mock)."""
from dataclasses import asdict, dataclass, field
from typing import Literal, Optional

Channel = Literal["ussd", "android", "ios", "web"]
SessionOutcome = Literal["completed", "dropped", "timeout", "error"]
PaymentStatus = Literal["paid", "due", "overdue", "trial", "not_set_up"]


@dataclass
class Organisation:
    id: int
    name: str
    edition: str
    plan_key: Optional[str]
    status: str


@dataclass
class Member:
    name: str
    phone: str  # +254...
    group: Optional[str]
    role: str
    status: Literal["active", "pending", "inactive"]


@dataclass
class Notice:
    id: int
    title: str
    sent: int
    delivered: int
    viewed: int
    created_at: int


@dataclass
class UssdSession:
    timestamp: int
    outcome: SessionOutcome


@dataclass
class Lookup:
    timestamp: int
    channel: Channel


@dataclass
class Payment:
    mpesa_ref: Optional[str]
    amount_cents: int
    status: PaymentStatus
    due_on: Optional[str]  # YYYY-MM-DD, Africa/Nairobi


@dataclass
class Series:
    name: str
    values: list  # one number per point


@dataclass
class Card:
    """What the dashboard page receives for one card (sent to the browser as JSON)."""
    key: str
    title: str
    subtitle: str
    kind: Literal["line", "kpis", "donut", "table", "billing"]
    labels: list = field(default_factory=list)
    series: list = field(default_factory=list)  # [Series]
    data: dict = field(default_factory=dict)
    unit: str = ""  # "%" for percentages
    source: Literal["live", "demo"] = "live"
    note: str = ""

    def to_json(self):
        out = asdict(self)
        out["empty"] = self.is_empty()
        return out

    def is_empty(self):
        if self.kind == "line":
            return not any(any(v for v in s["values"] if v) for s in [asdict(x) if not isinstance(x, dict) else x for x in self.series])
        if self.kind == "donut":
            return not any(v for v in self.data.get("values", []))
        if self.kind == "table":
            return not self.data.get("rows")
        return False


def per_quarter_hour(minutes):
    """1440 per-minute counts -> 96 values: failures per minute, averaged over each 15 minutes (readable as a line)."""
    return [round(sum(minutes[i:i + 15]) / 15, 2) for i in range(0, len(minutes), 15)]
