"""Hold events written by BE3 (see the "holds report" spec handed to BE1).

  REQUEST_NEEDS_INPUT       parse left a question an agent must not answer by itself     BUYER
  ATTRIBUTE_NOT_IN_CATALOG  the request named a condition the catalogue cannot check     BOTH
  CANDIDATES_BLOCKED        node 2 flagged products red, one event per rule per attempt  BUYER

buyer_id, agent_id and reason_code are passed both as arguments (new audit_log columns) and inside
the payload, so the hash chain covers them.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from ..internal import AuditApi, ProductRecord
from ..schemas import Violation
from .parser import ParseOutcome

# Questions that mean "do not order yet"; agent/buyer_agent.py stops on the same set.
BLOCKING_REASONS = {   # ambiguity field -> reason_code, in priority order
    "quantity": "NEEDS_QUANTITY",
    "category": "NEEDS_CATEGORY",
    "requirements": "NEEDS_REQUIREMENTS",
    "delivery_date": "NEEDS_DELIVERY_DATE",
}
MAX_ATTRIBUTE = 40


@dataclass
class HoldCaller:
    """Who asked, as the hold events need it (built from channel.identity.caller())."""
    buyer_id: Optional[str]
    agent_id: Optional[str]
    actor: str                      # "agent:<id>" or "user:<role>"


def normalize_attribute(text: str) -> str:
    return " ".join(text.lower().split())[:MAX_ATTRIBUTE]


def record_parse(audit: AuditApi, outcome: ParseOutcome, caller: HoldCaller) -> int:
    """Write the hold events for one parse. Returns how many were written. No request exists yet."""
    ids = dict(buyer_id=caller.buyer_id, agent_id=caller.agent_id)
    written = 0
    fields = [f for f in BLOCKING_REASONS if any(a["field"] == f for a in outcome.ambiguities)]
    if fields:
        reason = BLOCKING_REASONS[fields[0]]
        audit.append("REQUEST_NEEDS_INPUT", caller.actor,
                     {**ids, "reason_code": reason, "fields": fields, "parser": outcome.source},
                     request_id=None, visibility="BUYER", summary=f"Asked about {', '.join(fields)} before ordering",
                     reason_code=reason, **ids)
        written += 1
    for attribute in dict.fromkeys(normalize_attribute(a) for a in outcome.unknown_attributes):
        audit.append("ATTRIBUTE_NOT_IN_CATALOG", caller.actor,
                     {**ids, "reason_code": "ATTRIBUTE_NOT_IN_CATALOG", "attribute": attribute},
                     request_id=None, visibility="BOTH",
                     summary=f"'{attribute}' is not an attribute the catalogue can check",
                     reason_code="ATTRIBUTE_NOT_IN_CATALOG", **ids)
        written += 1
    return written


def record_blocked(audit: AuditApi, request_id: str, attempt: int,
                   blocked: list[tuple[ProductRecord, list[Violation]]],
                   buyer_id: Optional[str], agent_id: Optional[str], actor: str) -> int:
    """One CANDIDATES_BLOCKED event per violated rule in this attempt. Returns how many were written."""
    by_rule: dict[str, list[str]] = defaultdict(list)
    for product, violations in blocked:
        for v in violations:
            if product.id not in by_rule[v.rule]:
                by_rule[v.rule].append(product.id)
    ids = dict(buyer_id=buyer_id, agent_id=agent_id)
    for rule, product_ids in by_rule.items():
        audit.append("CANDIDATES_BLOCKED", actor,
                     {**ids, "reason_code": rule, "attempt": attempt, "product_ids": product_ids,
                      "count": len(product_ids)},
                     request_id=request_id, visibility="BUYER",
                     summary=f"{len(product_ids)} product(s) left out: {rule}", reason_code=rule, **ids)
    return len(by_rule)
