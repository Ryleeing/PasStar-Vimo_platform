"""A buyer agent that works from a natural-language need, using only the public API.

Flow: /requests/parse (understand) -> /requests -> choose -> /select -> /orders -> follow the order.
It prefers a candidate that can be ordered automatically, so it does not create approval work
when a good automatic option exists. If nothing comes back, it changes one condition and asks
again: for an agent, "this is wrong" means re-querying with new conditions, not rejecting nodes.

The choice policy is rule-based for now; an LLM decision step can replace `choose()` later.

Usage (server running on localhost:8000):
  python agent/buyer_agent.py "1000 litres of lactose-free milk by Monday"
  python agent/buyer_agent.py "..." --protocol prot_hotel_ext_v1     # external approval
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from typing import Optional

from pass_client import ApiError, Outcome, PassClient, finish_order, print_outcome

MAX_REQUERIES = 1
# Questions the agent must not answer by itself: guessing these would order the wrong thing.
BLOCKING_FIELDS = {"quantity", "category", "requirements"}


def choose(candidates: list[dict]) -> Optional[dict]:
    """Highest-ranked candidate that can be ordered automatically; otherwise the highest-ranked one."""
    selectable = sorted((c for c in candidates if c["selectable"]), key=lambda c: c["rank"])
    auto = [c for c in selectable if c["outcome_preview"] == "AUTO"]
    return (auto or selectable or [None])[0]


def relax_for_requery(structured: dict) -> Optional[tuple[dict, str]]:
    """One condition the agent may change on its own. Hard requirements are never touched."""
    if structured.get("substitution") == "NONE":
        return {**structured, "substitution": "SAME_CATEGORY"}, "allowed same-category substitutes"
    later = date.fromisoformat(structured["delivery_date"]) + timedelta(days=1)
    return {**structured, "delivery_date": later.isoformat()}, f"moved delivery to {later}"


def run(client: PassClient, text: str, protocol_id: str, profile_id: Optional[str] = None, **finish) -> Outcome:
    out = Outcome()
    try:
        parsed = client.parse(text, profile_id)
        structured = out.structured = parsed["structured"]
        out.log("Understood the need", f"{structured.get('category_label') or structured['category']}, "
                                       f"{structured['quantity']['value']:g} {structured['quantity']['unit']}, "
                                       f"by {structured['delivery_date']}")
        blocking = [a for a in parsed.get("ambiguities", []) if a["field"] in BLOCKING_FIELDS]
        for a in parsed.get("ambiguities", []):
            if a not in blocking:
                out.log("Open question, keeping the default", a["question"])
        if blocking:
            for a in blocking:
                out.log("Needs an answer before ordering", a["question"])
            out.stopped = "NEEDS_INPUT"
            return out

        for attempt in range(MAX_REQUERIES + 1):
            result = client.create_request(protocol_id, structured, profile_id, raw_text=text)
            out.log("Sent request", f"{result['request_id']}: {len(result['candidates'])} candidates, "
                                    f"{len(result['blocked'])} blocked")
            pick = choose(result["candidates"])
            if pick:
                break
            change = relax_for_requery(structured) if attempt < MAX_REQUERIES else None
            if not change:
                out.log("Gave up", result.get("no_candidate_reason") or "no candidates")
                out.stopped = "NO_CANDIDATES"
                return out
            structured, why = change
            out.log("No candidates, asking again", why)

        out.log("Chose a candidate", f"#{pick['rank']} {pick['id']} ({pick['lines'][0]['name']}), "
                                     f"expected {pick['outcome_preview']}: {pick['explanation']}")
        sel = client.select(result["request_id"], pick["id"])
        out.log("Got a signed quote", f"{sel['quote']['id']}, gate says {sel['gate']['outcome']}")
    except ApiError as e:
        out.error = e
        out.log("API error", str(e))
        return out
    return finish_order(client, sel["quote"]["id"], protocol_id, out, **finish)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text")
    ap.add_argument("--protocol", default="prot_hotel_v1")
    ap.add_argument("--profile")
    ap.add_argument("--url")
    args = ap.parse_args()
    print_outcome(run(PassClient(base_url=args.url), args.text, args.protocol, args.profile))


if __name__ == "__main__":
    main()
