"""Explanations for chain nodes and candidates.

TemplateExplain builds sentences from the disclosable pack without an LLM.
LlmExplain writes every sentence of one attempt in a single call to a light model, and falls
back to the template for anything the model leaves out or gets wrong, so an attempt always
has complete text. Only fields inside DisclosablePack.data may be used, by either.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from pydantic import BaseModel

from ..internal import DisclosablePack
from .llm import LlmClient

log = logging.getLogger(__name__)
MAX_SENTENCE = 280


def _euros(cents: int) -> str:
    return f"€{cents / 100:,.2f}"


class TemplateExplain:
    def node_summary(self, node: int, pack: DisclosablePack) -> str:
        d = pack.data
        if node == 1:
            reqs = ", ".join(d["requirements"]) or "no extra requirements"
            profile = f"; profile '{d['profile']}' applied" if d.get("profile") else ""
            what = f"{d['category']}, {reqs}, {d['quantity']}, delivered by {d['delivery_date']}{profile}"
            if d.get("submitted_by_agent") and d.get("parsed_from_text"):
                return f"Understood the message agent {d['submitted_by_agent']} passed on: {what}."
            if d.get("submitted_by_agent"):
                return f"Agent {d['submitted_by_agent']} sent a structured request (not interpreted here): {what}."
            return f"You need: {what}."
        if node == 2:
            return (f"Kept {d['kept']} products that meet every hard requirement; "
                    f"{d['blocked']} left out because they break one of your rules, {d['dropped']} not relevant.")
        if node == 3:
            if not d["count"]:
                if not d.get("converted", 1):
                    return "No product could be converted to your quantity."
                if not d.get("priced", 1):
                    return (f"Valio returned no prices for the {d['converted']} matching products for this buyer, "
                            "so none could be offered.")
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

    def explain_attempt(self, nodes: dict[int, DisclosablePack],
                        candidates: list[DisclosablePack]) -> tuple[dict[int, str], list[str]]:
        """All sentences for one attempt: {node: summary} and one explanation per candidate, in order."""
        return ({n: self.node_summary(n, p) for n, p in nodes.items()},
                [self.candidate_explanation(p) for p in candidates])


class _Sentence(BaseModel):
    id: str
    text: str


class _AttemptText(BaseModel):
    node_summaries: list[_Sentence]
    candidate_explanations: list[_Sentence]


EXPLAIN_SYSTEM = """You write the short texts a professional kitchen's buyer sees next to an automated purchase.
You get the facts of one purchase attempt as JSON: the processing steps ("nodes") and the options found
("candidates"). Write one plain English sentence for each node and each candidate, keyed by its id.

- Node 1 says what was understood, 2 what the catalogue search kept and flagged, 3 how options were ranked,
  4 what the protocol check means for the top option.
- A candidate sentence says why someone would pick it, or for a flagged one (it has violations) why it
  cannot be picked.
- Use only facts in the JSON. Never invent numbers, products, prices or reasons. Money is in cents in the
  data; write it in euros (e.g. 96475 -> €964.75).
- At most 30 words per sentence, no marketing tone."""


class LlmExplain(TemplateExplain):
    """One light-model call per attempt; the template fills any gap."""

    def __init__(self, llm: Optional[LlmClient] = None) -> None:
        self.llm = llm or LlmClient(task="explain")

    def explain_attempt(self, nodes: dict[int, DisclosablePack],
                        candidates: list[DisclosablePack]) -> tuple[dict[int, str], list[str]]:
        summaries, explanations = super().explain_attempt(nodes, candidates)
        payload = {"nodes": {f"node_{n}": p.data for n, p in nodes.items()},
                   "candidates": {f"cand_{i}": p.data for i, p in enumerate(candidates)}}
        out = self.llm.structured(EXPLAIN_SYSTEM, json.dumps(payload, ensure_ascii=False, default=str),
                                  _AttemptText, "attempt_text")
        if out is None:
            return summaries, explanations
        for s in out.node_summaries:
            n = int(s.id.removeprefix("node_")) if s.id.removeprefix("node_").isdigit() else None
            if n in summaries and _usable(s.text):
                summaries[n] = s.text.strip()
        for s in out.candidate_explanations:
            i = int(s.id.removeprefix("cand_")) if s.id.removeprefix("cand_").isdigit() else None
            if i is not None and 0 <= i < len(explanations) and _usable(s.text):
                explanations[i] = s.text.strip()
        return summaries, explanations


def _usable(text: str) -> bool:
    return 0 < len(text.strip()) <= MAX_SENTENCE
