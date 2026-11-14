"""Agents that break the rules on purpose, to show the channel stops them.

Two personas:
  - an unregistered bot (agt_rogue: registered to the hotel but named in no protocol), refused at the door;
  - a legitimate agent going rogue inside its own protocol, so each probe reaches the specific check it tests.

Each probe states which error the contract requires. The report shows whether the server blocked it with
that check, so it doubles as an acceptance checklist for BE1 / BE2.

Usage (server running on localhost:8000):
  python agent/rogue_agent.py
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Optional

from pass_client import ApiError, PassClient

ROGUE_KEY = "demo-rogue"            # seeded as agt_rogue: registered to the hotel, named in no protocol
HOTEL_KEY, EXT_KEY = "demo-hotel", "demo"
FORGED_JWS = "eyJhbGciOiJFZERTQSJ9.eyJ0eXAiOiJidXllci1hdXRob3JpemF0aW9uL3YxIn0.AAAA"


@dataclass
class Probe:
    name: str
    expect: tuple[str, ...]          # any of these error codes counts as blocked by the intended check
    act: Callable[[PassClient], object]


@dataclass
class ProbeResult:
    probe: Probe
    got: str                          # error code, or "ALLOWED"

    @property
    def blocked(self) -> bool:
        return self.got in self.probe.expect


def _structured(qty: float = 1000, category: str = "10400") -> dict:
    return {"category": category, "quantity": {"value": qty, "unit": "L"},
            "requirements": [{"kind": "CLAIM", "code": "FREE_FROM_LACTOSE"}],
            "delivery_date": (date.today() + timedelta(days=2)).isoformat()}


def _as(c: PassClient, key: str = "", user: Optional[str] = None) -> PassClient:
    return PassClient(http=c.http, agent_key=key, demo_user=user)


def _quote(c: PassClient, protocol: str, qty: float, profile: Optional[str] = None) -> dict:
    """Request and select the top candidate; returns the signed quote."""
    r = c.create_request(protocol, _structured(qty), profile)
    return c.select(r["request_id"], r["candidates"][0]["id"])["quote"]


def _select_blocked(c: PassClient) -> object:
    hotel = _as(c, HOTEL_KEY)
    r = hotel.create_request("prot_hotel_v1", _structured(), "prof_banquet")   # the profile excludes a brand
    if not r["blocked"]:
        raise ApiError(0, "NOTHING_BLOCKED", "no red-flagged candidate to try", {})
    return hotel.select(r["request_id"], r["blocked"][0]["id"])


def _probe_stock(c: PassClient) -> None:
    """Binary-search the stock of one product with ever-changing quantities."""
    hotel, lo, hi = _as(c, HOTEL_KEY), 1, 100_000
    for _ in range(8):
        mid = (lo + hi) // 2
        _quote(hotel, "prot_hotel_v1", mid)
        lo = mid


def _flood(c: PassClient) -> None:
    hotel = _as(c, HOTEL_KEY)
    for _ in range(40):
        hotel.create_request("prot_hotel_v1", _structured())


def _forged_authorization(c: PassClient) -> object:
    ext = _as(c, EXT_KEY)
    q = _quote(ext, "prot_hotel_ext_v1", 1200)                       # over the automatic limit
    return ext.place_order(q["id"], "prot_hotel_ext_v1", buyer_authorization=FORGED_JWS)


def _self_approval(c: PassClient) -> object:
    chef = _as(c, user="usr_chef")
    q = _quote(chef, "prot_hotel_v1", 1200)                           # over the limit: needs the manager
    order = chef.place_order(q["id"], "prot_hotel_v1")
    if not order.get("approval_id"):
        raise ApiError(0, "NO_APPROVAL", f"order went to {order['status']}, nothing to approve", {})
    return chef.call("POST", f"/v1/approvals/{order['approval_id']}/decision", {"decision": "APPROVE"})


PROBES = [
    # the unregistered bot
    Probe("Call without any identity", ("OUT_OF_SCOPE", "PROTOCOL_INVALID"),
          lambda c: _as(c).create_request("prot_hotel_v1", _structured())),
    Probe("Unregistered bot uses the hotel's protocol", ("PROTOCOL_INVALID",),
          lambda c: _as(c, ROGUE_KEY).create_request("prot_hotel_v1", _structured())),
    # a registered agent going rogue
    Probe("Use another buyer's protocol", ("PROTOCOL_INVALID", "OUT_OF_SCOPE"),
          lambda c: _as(c, HOTEL_KEY).create_request("prot_hospital_v1", _structured())),
    Probe("Buy outside the allowed categories", ("OUT_OF_SCOPE",),
          lambda c: _as(c, HOTEL_KEY).create_request("prot_hotel_v1", _structured(category="22540"))),
    Probe("Select a red-flagged candidate", ("OUT_OF_SCOPE", "VALIDATION_ERROR"), _select_blocked),
    Probe("Ask for exact warehouse stock (L2)", ("OUT_OF_SCOPE", "NOT_FOUND"),
          lambda c: _as(c, HOTEL_KEY).call("GET", "/v1/products/464179/stock")),
    Probe("Order with a made-up quote", ("NOT_FOUND", "QUOTE_EXPIRED", "QUOTE_CHANGED"),
          lambda c: _as(c, HOTEL_KEY).place_order("quo_forged", "prot_hotel_v1")),
    Probe("Forge a buyer authorization", ("AUTHORIZATION_INVALID",), _forged_authorization),
    Probe("Requester approves their own order", ("SELF_APPROVAL_FORBIDDEN", "NOT_APPROVER"), _self_approval),
    Probe("Probe stock with many quantities", ("PROBE_LIMIT",), _probe_stock),
    Probe("Flood requests past the rate limit", ("RATE_LIMITED",), _flood),       # last: it uses up the rate limit
]


def run(client: PassClient, probes: Optional[list[Probe]] = None) -> list[ProbeResult]:
    results = []
    for p in probes or PROBES:
        try:
            p.act(client)
            got = "ALLOWED"
        except ApiError as e:
            got = e.code
        results.append(ProbeResult(p, got))
    return results


def print_report(results: list[ProbeResult]) -> None:
    width = max(len(r.probe.name) for r in results)
    for r in results:
        mark = "BLOCKED " if r.blocked else "NOT BLOCKED"
        print(f"{mark:<11}  {r.probe.name:<{width}}  got {r.got:<24} expected {' / '.join(r.probe.expect)}")
    print(f"\n{sum(r.blocked for r in results)}/{len(results)} misbehaviours blocked by the intended check")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url")
    args = ap.parse_args()
    print_report(run(PassClient(base_url=args.url)))


if __name__ == "__main__":
    main()
