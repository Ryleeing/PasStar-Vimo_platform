"""Explanations for chain nodes and candidates.

TemplateExplain builds sentences from the disclosable pack without an LLM. It is the
fallback the LLM version (later) drops back to when the model fails or times out.
Only fields inside DisclosablePack.data may be used.
"""
from __future__ import annotations

from ..internal import DisclosablePack


def _euros(cents: int) -> str:
    return f"€{cents / 100:,.2f}"


class TemplateExplain:
    def node_summary(self, node: int, pack: DisclosablePack) -> str:
        d = pack.data
        if node == 1:
            reqs = ", ".join(d["requirements"]) or "no extra requirements"
            profile = f"; profile '{d['profile']}' applied" if d.get("profile") else ""
            what = f"{d['category']}, {reqs}, {d['quantity']}, delivered by {d['delivery_date']}{profile}"
            if d.get("submitted_by_agent"):
                return f"Agent {d['submitted_by_agent']} sent a structured request (not interpreted here): {what}."
            return f"You need: {what}."
        if node == 2:
            return (f"Kept {d['kept']} products that meet every hard requirement; "
                    f"{d['blocked']} flagged red, {d['dropped']} not relevant.")
        if node == 3:
            if not d["count"]:
                return "No product could be priced and converted to your quantity."
            return f"Built {d['count']} options ranked by your weights ({d['weights']}). #1 is {d['top']}."
        if node == 4:
            if not d.get("top_outcome"):
                return "Nothing to check against the protocol."
            return f"#1 costs {_euros(d['top_gross_cents'])}: {d['top_outcome_text']}."
        raise ValueError(f"unknown node {node}")

    def candidate_explanation(self, pack: DisclosablePack) -> str:
        d = pack.data
        if d.get("violations"):
            return "Not selectable: " + "; ".join(v["detail"] for v in d["violations"]) + "."
        lead = {"RECOMMENDED": "Best match for your priorities", "CHEAPEST": "Lowest total",
                "MOST_RELIABLE": "Most reliable delivery"}.get(d.get("badge") or "", "Another option")
        reasons = "; ".join(d.get("outcome_reasons") or [])
        return f"{lead}: {d['lines'][0]['name']}" + (f". {reasons}." if reasons else ".")
