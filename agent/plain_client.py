"""A plain script that buys through The Pass without any AI.

It proves the channel is usable by ordinary software: send a structured request,
take the top-ranked selectable candidate, order it, and follow the order to the end.

Usage (server running on localhost:8000):
  python agent/plain_client.py                                   # 1000 L lactose-free milk, hosted approval
  python agent/plain_client.py --protocol prot_hotel_ext_v1      # external approval via the buyer's system
  python agent/plain_client.py --request my_request.json         # your own StructuredRequest JSON
"""
from __future__ import annotations

import argparse
import json
import pathlib
from datetime import date, timedelta
from typing import Optional

from pass_client import ApiError, Outcome, PassClient, finish_order, print_outcome

DEFAULT_REQUEST = {
    "category": "10400", "category_label": "Milk",
    "requirements": [{"kind": "CLAIM", "code": "FREE_FROM_LACTOSE", "label": "Lactose-free"}],
    "quantity": {"value": 1000, "unit": "L"},
    "delivery_date": (date.today() + timedelta(days=2)).isoformat(),
    "substitution": "SAME_CATEGORY",
}


def run(client: PassClient, protocol_id: str, structured: dict, profile_id: Optional[str] = None, **finish) -> Outcome:
    out = Outcome()
    try:
        result = client.create_request(protocol_id, structured, profile_id)
    except ApiError as e:
        out.error = e
        out.log("API error", str(e))
        return out
    out.log("Sent structured request", f"{result['request_id']}: {len(result['candidates'])} candidates, "
                                       f"{len(result['blocked'])} blocked")
    selectable = [c for c in result["candidates"] if c["selectable"]]
    if not selectable:
        out.log("No candidate to choose", result.get("no_candidate_reason") or "")
        return out
    top = selectable[0]
    out.log("Chose the top-ranked candidate", f"{top['id']} ({top['lines'][0]['name']}), "
                                              f"expected outcome {top['outcome_preview']}")
    try:
        sel = client.select(result["request_id"], top["id"])
    except ApiError as e:
        out.error = e
        out.log("API error", str(e))
        return out
    out.log("Got a signed quote", f"{sel['quote']['id']}, gate says {sel['gate']['outcome']}")
    return finish_order(client, sel["quote"]["id"], protocol_id, out, **finish)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--protocol", default="prot_hotel_v1")
    ap.add_argument("--profile")
    ap.add_argument("--request", type=pathlib.Path, help="StructuredRequest JSON file")
    ap.add_argument("--url")
    args = ap.parse_args()
    structured = json.loads(args.request.read_text()) if args.request else DEFAULT_REQUEST
    print_outcome(run(PassClient(base_url=args.url), args.protocol, structured, args.profile))


if __name__ == "__main__":
    main()
