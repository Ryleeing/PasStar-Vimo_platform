"""Hold report (GET /v1/reports/holds): what the channel held or blocked, read from the audit log.

Five audit events count as holds. BE3 writes REQUEST_NEEDS_INPUT, ATTRIBUTE_NOT_IN_CATALOG and CANDIDATES_BLOCKED;
BE1 writes ORDER_HELD (orders.py) and AGENT_BLOCKED (checkpoints.py). A buyer sees its own entries, item by item.
Valio sees counts only, across buyers: no summaries, amounts or order ids, and a missing attribute is listed only
when at least two buyers asked for it, so one buyer's wording cannot be read back from the report.

The response models live here, with the route's owner, not in the shared schemas.py."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Literal, Optional, get_args

from pydantic import BaseModel

from .. import db
from ..errors import ApiError
from . import checkpoints
from .identity import Caller

HoldEvent = Literal["REQUEST_NEEDS_INPUT", "ATTRIBUTE_NOT_IN_CATALOG", "CANDIDATES_BLOCKED", "ORDER_HELD", "AGENT_BLOCKED"]
EVENTS = get_args(HoldEvent)
MAX_RANGE = timedelta(days=90)
MAX_ITEMS = 200


class ReasonCount(BaseModel):
    reason_code: str
    count: int


class AgentHolds(BaseModel):
    agent_id: str
    total: int
    by_reason: list[ReasonCount]


class HoldItem(BaseModel):
    """One held event, buyer view only."""
    seq: int
    at: datetime
    event: HoldEvent
    reason_code: str
    summary: str
    request_id: Optional[str] = None
    order_id: Optional[str] = None
    agent_id: Optional[str] = None


class AttributeCount(BaseModel):
    attribute: str
    count: int


class HoldsReport(BaseModel):
    audience: Literal["BUYER", "VALIO"]
    buyer_id: Optional[str] = None                     # set in the buyer view
    since: datetime
    until: datetime
    total: int
    by_event: dict[HoldEvent, int]
    by_reason: list[ReasonCount]                       # most frequent first
    by_agent: list[AgentHolds]                         # most held first
    items: list[HoldItem] = []                         # buyer view only, newest first, at most 200
    missing_attributes: list[AttributeCount] = []      # Valio view only, attributes asked for by at least 2 buyers


def _reasons(counts: Counter) -> list[ReasonCount]:
    return [ReasonCount(reason_code=code, count=n) for code, n in counts.most_common()]


def holds(who: Caller, since: Optional[datetime], until: Optional[datetime], buyer_id: Optional[str],
          agent_id: Optional[str]) -> HoldsReport:
    until = (until or db.now()).astimezone()
    since = since.astimezone() if since else until - timedelta(days=7)
    if until - since > MAX_RANGE:
        raise ApiError("VALIDATION_ERROR", f"A report covers at most {MAX_RANGE.days} days", since=since.isoformat(),
                       until=until.isoformat())
    valio = who.kind == "VALIO_USER"
    if not valio:
        buyer_id = who.buyer_id                        # a buyer reads its own entries, whatever it asks for

    checkpoints.flush_blocks()
    rows = db.all(f"SELECT * FROM audit_log WHERE event IN ({','.join('?' * len(EVENTS))}) AND visibility IN (?, 'BOTH')"
                  " ORDER BY seq DESC", (*EVENTS, "VALIO" if valio else "BUYER"))
    rows = [r for r in rows if since <= datetime.fromisoformat(r["at"]) < until
            and buyer_id in (None, r["buyer_id"]) and agent_id in (None, r["agent_id"])]

    by_event, by_reason, by_agent = Counter(), Counter(), defaultdict(Counter)
    asked, askers, items = Counter(), defaultdict(set), []
    for r in rows:
        body = json.loads(r["payload_json"])
        if r["event"] == "ATTRIBUTE_NOT_IN_CATALOG":   # one entry per unknown attribute, already normalised by BE3
            asked[body["payload"]["attribute"]] += 1
            if r["buyer_id"]:                          # BE3 may not know the buyer yet; unknown buyers are not counted
                askers[body["payload"]["attribute"]].add(r["buyer_id"])
            if valio:
                continue                               # for Valio these appear in missing_attributes only
        n = 1 + body["payload"].get("suppressed", 0)   # an AGENT_BLOCKED entry stands for the blocks it suppressed too
        by_event[r["event"]] += n
        by_reason[r["reason_code"]] += n
        if r["agent_id"]:
            by_agent[r["agent_id"]][r["reason_code"]] += n
        items.append(HoldItem(seq=r["seq"], at=r["at"], event=r["event"], reason_code=r["reason_code"],
                              summary=body["summary"], request_id=r["request_id"],
                              order_id=body["payload"].get("order_id"), agent_id=r["agent_id"]))

    agents = sorted(by_agent.items(), key=lambda a: -a[1].total())
    return HoldsReport(
        audience="VALIO" if valio else "BUYER", buyer_id=None if valio else buyer_id, since=since, until=until,
        total=by_event.total(), by_event=by_event, by_reason=_reasons(by_reason),
        by_agent=[AgentHolds(agent_id=a, total=c.total(), by_reason=_reasons(c)) for a, c in agents],
        items=[] if valio else items[:MAX_ITEMS],
        missing_attributes=[AttributeCount(attribute=a, count=n) for a, n in asked.most_common() if len(askers[a]) >= 2]
        if valio else [])


# ───── Examples for the frontend (fixtures/holds_buyer.json, fixtures/holds_valio.json) ─────

_SINCE, _UNTIL = datetime.fromisoformat("2026-11-07T00:00:00+02:00"), datetime.fromisoformat("2026-11-14T12:00:00+02:00")
_HOTEL = [ReasonCount(reason_code="NEEDS_QUANTITY", count=2), ReasonCount(reason_code="PROFILE_EXCLUDED_BRAND", count=2),
          ReasonCount(reason_code="R-SPEND-APPROVAL", count=1), ReasonCount(reason_code="R-DELIVERY-PARTIAL", count=1)]
_ROGUE = [ReasonCount(reason_code="RATE_LIMITED", count=31), ReasonCount(reason_code="PROBE_LIMIT", count=12),
          ReasonCount(reason_code="OUT_OF_SCOPE", count=4)]

EXAMPLE_BUYER = HoldsReport(
    audience="BUYER", buyer_id="buy_hotel", since=_SINCE, until=_UNTIL, total=6,
    by_event={"REQUEST_NEEDS_INPUT": 2, "CANDIDATES_BLOCKED": 2, "ORDER_HELD": 2}, by_reason=_HOTEL,
    by_agent=[AgentHolds(agent_id="agt_hotel", total=6, by_reason=_HOTEL)],
    items=[HoldItem(seq=41, at=datetime.fromisoformat("2026-11-14T10:02:11+02:00"), event="ORDER_HELD",
                    reason_code="R-SPEND-APPROVAL", request_id="req_1", order_id="ord_3", agent_id="agt_hotel",
                    summary="Held for approval: Total €1,078.25 is above the auto-approve limit of €1,000.00")])
EXAMPLE_VALIO = HoldsReport(
    audience="VALIO", since=_SINCE, until=_UNTIL, total=61, by_event={"ORDER_HELD": 14, "AGENT_BLOCKED": 47},
    by_reason=_ROGUE + [ReasonCount(reason_code="R-SPEND-APPROVAL", count=9), ReasonCount(reason_code="R-OUT-OF-API", count=5)],
    by_agent=[AgentHolds(agent_id="agt_rogue", total=47, by_reason=_ROGUE),
              AgentHolds(agent_id="agt_hotel", total=14, by_reason=[ReasonCount(reason_code="R-SPEND-APPROVAL", count=9),
                                                                    ReasonCount(reason_code="R-OUT-OF-API", count=5)])],
    missing_attributes=[AttributeCount(attribute="keto", count=5)])
