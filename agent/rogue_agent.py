"""An agent that breaks the rules on purpose, to show the channel stops it.

Each probe does one thing a careless or hostile agent might try and states which error
the contract requires. The report shows whether the server actually blocked it, so it
doubles as a checklist while BE1 / BE2 replace the mock server.

Usage (server running on localhost:8000):
  python agent/rogue_agent.py
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Optional

from pass_client import ApiError, PassClient

ROGUE_KEY = "agt_rogue"
FORGED_JWS = "eyJhbGciOiJFZERTQSJ9.eyJ0eXAiOiJidXllci1hdXRob3JpemF0aW9uL3YxIn0.AAAA"


@dataclass
class Probe:
    name: str
    expect: tuple[str, ...]          # any of these error codes counts as blocked
    act: Callable[[PassClient], object]


@dataclass
class ProbeResult:
    probe: Probe
    got: str                          # error code, or "ALLOWED"

    @property
    def blocked(self) -> bool:
        return self.got in self.probe.expect


def _request(category: str = "10400", qty: float = 1000, protocol: str = "prot_hotel_v1") -> Callable[[PassClient], object]:
    structured = {"category": category, "quantity": {"value": qty, "unit": "L"},
                  "delivery_date": (date.today() + timedelta(days=2)).isoformat()}
    return lambda c: c.create_request(protocol, structured)


def _probe_stock(c: PassClient) -> None:
    """Binary-search the stock of one product with ever-changing quantities."""
    lo, hi = 1, 100_000
    for _ in range(8):
        mid = (lo + hi) // 2
        _request(qty=mid)(c)
        lo = mid


def _select_blocked(c: PassClient) -> object:
    r = _request()(c)
    target = (r["blocked"] or [{"id": "cand_x1"}])[0]["id"]
    return c.select(r["request_id"], target)


def _flood(c: PassClient) -> None:
    for _ in range(40):
        _request()(c)


PROBES = [
    Probe("Call without an agent key", ("PROTOCOL_INVALID", "OUT_OF_SCOPE"),
          lambda c: PassClient(http=c.http, agent_key="").create_request("prot_hotel_v1", {
              "category": "10400", "quantity": {"value": 10, "unit": "L"},
              "delivery_date": date.today().isoformat()})),
    Probe("Use another buyer's protocol", ("PROTOCOL_INVALID",), _request(protocol="prot_hospital_v1")),
    Probe("Buy outside the allowed categories", ("OUT_OF_SCOPE",), _request(category="22540")),
    Probe("Select a red-flagged candidate", ("VALIDATION_ERROR", "OUT_OF_SCOPE"), _select_blocked),
    Probe("Ask for exact warehouse stock (L2)", ("OUT_OF_SCOPE", "NOT_FOUND"),
          lambda c: c.call("GET", "/v1/products/464179/stock")),
    Probe("Probe stock with many quantities", ("PROBE_LIMIT",), _probe_stock),
    Probe("Flood requests past the rate limit", ("RATE_LIMITED",), _flood),
    Probe("Order with a made-up quote", ("NOT_FOUND", "QUOTE_EXPIRED", "QUOTE_CHANGED"),
          lambda c: c.place_order("quo_forged", "prot_hotel_v1")),
    Probe("Forge a buyer authorization", ("AUTHORIZATION_INVALID",),
          lambda c: c.place_order("quo_7", "prot_hotel_ext_v1", buyer_authorization=FORGED_JWS)),
    Probe("Approve its own order as the requester", ("SELF_APPROVAL_FORBIDDEN", "NOT_APPROVER"),
          lambda c: PassClient(http=c.http, agent_key=ROGUE_KEY, demo_user="usr_chef").call(
              "POST", "/v1/approvals/apr_1/decision", {"decision": "APPROVE"})),
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
    print(f"\n{sum(r.blocked for r in results)}/{len(results)} misbehaviours blocked")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url")
    args = ap.parse_args()
    print_report(run(PassClient(base_url=args.url, agent_key=ROGUE_KEY)))


if __name__ == "__main__":
    main()
