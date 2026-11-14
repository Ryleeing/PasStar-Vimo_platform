"""Gate (GateApi): compares a proposal with the protocol rule by rule. Rule order and outcomes live in
config/rules.yaml; the conditions live here. Every rule that fires is listed in rules_hit and the final outcome
is the strictest one: REJECT > VALIO_REVIEW > BUYER_APPROVAL > AUTO."""
from __future__ import annotations

import pathlib
from functools import reduce
from typing import Optional

import yaml

from ..internal import EffectiveRules
from ..schemas import CandidateLine, GateDecision, Outcome, Quote, RuleHit
from . import audit, quotes

_CONFIG = pathlib.Path(__file__).parents[1] / "config"
RULES = yaml.safe_load((_CONFIG / "rules.yaml").read_text(encoding="utf-8"))
POLICY = yaml.safe_load((_CONFIG / "approval_policy.yaml").read_text(encoding="utf-8"))
SEVERITY = {"AUTO": 0, "BUYER_APPROVAL": 1, "VALIO_REVIEW": 2, "REJECT": 3}
# protocol value → gate outcome
_PROTOCOL_OUTCOME = {"AUTO": "AUTO", "APPROVAL": "BUYER_APPROVAL", "FORBIDDEN": "REJECT",
                     "REQUIRE_APPROVAL": "BUYER_APPROVAL", "REJECT": "REJECT"}
_TIER = {"R-SUB-CROSS": "CROSS_CATEGORY", "R-SUB-SAME": "SAME_CATEGORY", "R-SUB-OFFICIAL": "OFFICIAL"}


def eur(cents: int) -> str:
    return f"€{cents / 100:,.2f}"


def strictest(hits: list[RuleHit]) -> Outcome:
    return max((h.outcome for h in hits), key=SEVERITY.get, default="AUTO")


def _hit(rule_id: str, lines: list[CandidateLine], gross: int, rules: EffectiveRules, data_unverified: bool) -> Optional[str]:
    """Return a human-readable sentence when the rule fires, a falsy value otherwise."""
    if rule_id == "R-SPEND-MAX":
        return gross > rules.max_cents and f"Total {eur(gross)} is above the order maximum of {eur(rules.max_cents)}"
    if rule_id == "R-DELIVERY-NONE":
        return all(l.available_on_date == "NO" for l in lines) and "Nothing in this order can be delivered on the requested date"
    if rule_id == "R-OUT-OF-API":
        big = [l for l in lines if l.qty_covered.value > POLICY["custom_quote_line_qty"][l.qty_covered.unit]]
        return big and f"{big[0].qty_covered.value:g} {big[0].qty_covered.unit} of {big[0].name} is more than the API can quote; needs Valio sales"
    if rule_id == "R-DELIVERY-PARTIAL":
        part = [l for l in lines if l.available_on_date == "PARTIAL"]
        return part and f"Only part of {part[0].name} can be delivered on the requested date"
    if rule_id == "R-SPEND-APPROVAL":
        return gross > rules.auto_limit_cents and f"Total {eur(gross)} is above the auto-approve limit of {eur(rules.auto_limit_cents)}"
    if rule_id == "R-DATA-UNVERIFIED":
        return data_unverified and "A hard requirement is not confirmed by manufacturer data (unverified)"
    if rule_id == "R-QTY-INEXACT":
        unknown = [l for l in lines if l.conversion_source == "UNKNOWN"]
        return unknown and f"The quantity of {unknown[0].name} cannot be converted exactly"
    used = [l for l in lines if l.substitution_tier == _TIER[rule_id]]
    return used and f"{used[0].name} is a {_TIER[rule_id].replace('_', '-').lower()} substitute"


def preview(lines: list[CandidateLine], totals_gross_cents: int, rules: EffectiveRules, data_unverified: bool) -> GateDecision:
    """Work out "what happens if you pick this"; writes nothing."""
    hits = []
    for rule in RULES[:-1]:                                  # the last rule, R-SPEND-AUTO, is the fallback
        description = _hit(rule["id"], lines, totals_gross_cents, rules, data_unverified)
        if description:
            outcome = rule["outcome"]
            if outcome.startswith("protocol."):
                outcome = _PROTOCOL_OUTCOME[reduce(getattr, outcome.split(".")[1:], rules.protocol)]
            hits.append(RuleHit(rule_id=rule["id"], description=description, outcome=outcome))
    if strictest(hits) == "AUTO":
        hits.append(RuleHit(rule_id="R-SPEND-AUTO", outcome="AUTO",
                            description=f"Total {eur(totals_gross_cents)} is within the auto-approve limit of {eur(rules.auto_limit_cents)}"))
    outcome = strictest(hits)
    return GateDecision(outcome=outcome, rules_hit=hits,
                        summary="; ".join(h.description for h in hits if h.outcome == outcome))


def evaluate(quote: Quote, rules: EffectiveRules) -> GateDecision:
    """The binding decision; writes the audit log."""
    decision = preview(quote.lines, quote.totals.gross_cents, rules, quotes.internal(quote.id)["data_unverified"])
    audit.append("GATE_DECIDED", "system", {"buyer_id": rules.protocol.buyer_id, "quote_id": quote.id, **decision.model_dump()},
                 request_id=quote.request_id, summary=f"Gate: {decision.outcome}. {decision.summary}")
    return decision
