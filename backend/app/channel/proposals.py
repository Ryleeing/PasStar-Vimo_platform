"""Approve with changes (APPROVE_WITH_CHANGES): an approver never edits the order, only the "proposal parameters".

Every change goes back through the gateway: re-query price and stock → gate → a newly signed quote. Manual changes
and the automatic flow share one rule set, so a human-approved order is never looser than a machine-approved one.
The path is picked from who the approver is:
  - a change by a Valio reviewer is a new quote round (a counter-offer): the order stops at REQUOTE_REQUIRED and
    the buyer orders again with the new quote within the confirmation window;
  - a buyer approver changes their own order: if the new proposal passes and the unit prices are the ones they saw,
    the order continues on the new proposal; otherwise it needs confirmation as well.
Allergen data, the hard requirements of the buyer, data classification, risk tags, the agent profile, the system
suggestion and the original request text are read-only: they are not among the changeable fields.
Every change needs a reason; the diff is written to the audit log."""
from __future__ import annotations

import pathlib
from datetime import date

import yaml

from ..errors import ApiError
from ..schemas import ApprovalDecision, FieldChange, Order, Quantity, Quote
from . import audit, gate, protocols, quotes, requests_view

POLICY = yaml.safe_load((pathlib.Path(__file__).parents[1] / "config" / "approval_policy.yaml").read_text(encoding="utf-8"))
# proposal parameters an approver may change
FIELDS = {"BUYER": {"candidate_id", "delivery_date", "lines"},
          "VALIO": {"candidate_id", "delivery_date", "lines", "conditions", "valid_minutes"}}
LINE_FIELDS = {"BUYER": {"product_id", "sales_units"},
               "VALIO": {"product_id", "sales_units", "unit_price_cents"}}


def _only(given: dict, allowed: set[str]) -> None:
    extra = sorted(set(given) - allowed)
    if extra:
        raise ApiError("VALIDATION_ERROR", "These fields are read-only or cannot be changed by this approver", fields=extra)


def modify(kind: str, order: Order, d: ApprovalDecision, who: str) -> tuple[Quote | None, bool]:
    """Returns (the new quote, whether the order may continue on it right away).
    (None, False) means the change is beyond the reviewer's authority and was escalated."""
    c = d.changes
    _only(c, FIELDS[kind])
    quote = quotes.get(order.quote_id)
    protocol = protocols.get_active(quote.protocol_id, None)
    req = requests_view.get(quote.request_id)
    buyer_id = protocol.body.buyer_id
    diff: list[FieldChange] = []

    # substitutes: only from the candidates the gateway produced
    x = quotes.internal(quote.id)
    base, candidate_id, unverified, overrides = quote.lines, quote.candidate_id, x["data_unverified"], dict(x["price_overrides"])
    if "candidate_id" in c:
        cand = next((k for k in req.candidates if k.id == c["candidate_id"]), None)
        if cand is None:
            raise ApiError("VALIDATION_ERROR", "A substitute must be one of the gateway's selectable candidates")
        diff.append(FieldChange(field="candidate_id", before=candidate_id, after=cand.id))
        base, candidate_id, unverified, overrides = cand.lines, cand.id, quotes.unverified(cand), {}

    # quantity can only go down; price only within the reviewer band, beyond it the approval escalates
    lines = {l.product_id: l for l in base}
    for ch in c.get("lines", []):
        _only(ch, LINE_FIELDS[kind])
        l = lines.get(ch["product_id"])
        if l is None:
            raise ApiError("VALIDATION_ERROR", f"Product {ch['product_id']} is not in this proposal")
        if "sales_units" in ch:
            n = ch["sales_units"]
            if not 0 < n <= l.sales_units:
                raise ApiError("VALIDATION_ERROR", "Quantity can only be reduced", product_id=l.product_id, current=l.sales_units)
            diff.append(FieldChange(field=f"lines.{l.product_id}.sales_units", before=l.sales_units, after=n))
            covered = Quantity(value=l.qty_covered.value * n / l.sales_units, unit=l.qty_covered.unit)
            lines[l.product_id] = l.model_copy(update={"sales_units": n, "qty_covered": covered, "overbuy": None})
        if "unit_price_cents" in ch:
            p, band = ch["unit_price_cents"], POLICY["reviewer_price_band_pct"]
            if abs(p - l.unit_price_cents) * 100 > band * l.unit_price_cents:
                audit.append("APPROVAL_ESCALATED", who, {"buyer_id": buyer_id, "order_id": order.id, "product_id": l.product_id,
                                                         "from": l.unit_price_cents, "to": p, "band_pct": band, "reason": d.note},
                             request_id=order.request_id, visibility="VALIO",
                             summary=f"Price change beyond ±{band}% escalated to {POLICY['price_escalation_to']}")
                return None, False
            diff.append(FieldChange(field=f"lines.{l.product_id}.unit_price_cents", before=l.unit_price_cents, after=p))
            overrides[l.product_id] = p

    delivery_date = quote.delivery_date
    if "delivery_date" in c:
        delivery_date = date.fromisoformat(c["delivery_date"])
        diff.append(FieldChange(field="delivery_date", before=quote.delivery_date, after=delivery_date))
    minutes = c.get("valid_minutes", POLICY["counter_offer_valid_minutes"])
    if not 0 < minutes <= POLICY["max_valid_minutes"]:
        raise ApiError("VALIDATION_ERROR", f"valid_minutes must be between 1 and {POLICY['max_valid_minutes']}")
    conditions = c.get("conditions", x["conditions"])
    if conditions != x["conditions"]:
        diff.append(FieldChange(field="conditions", before=x["conditions"], after=conditions))

    # back through the gateway: re-query price and stock, run the gate again
    new_lines, totals = quotes.price(list(lines.values()), protocol, delivery_date, overrides)
    decision = gate.preview(new_lines, totals.gross_cents, protocols.effective_rules(protocol, req.profile), unverified)
    if decision.outcome == "REJECT":
        raise ApiError("VALIDATION_ERROR", f"The modified proposal does not pass the gateway: {decision.summary}",
                       rules=[h.rule_id for h in decision.rules_hit if h.outcome == "REJECT"])
    diff.append(FieldChange(field="totals.gross_cents", before=quote.totals.gross_cents, after=totals.gross_cents))

    reason = f"The approver changed the proposal: {d.note}"
    if kind == "VALIO":
        reason = f"Valio made a counter-offer: {d.note}" + (f" Conditions: {'; '.join(conditions)}." if conditions else "")
    # a proposal Valio changed needs no second Valio review; other levels are judged as usual
    new = quotes.issue(quote.request_id, candidate_id, protocol, new_lines, totals, delivery_date, unverified, overrides,
                       ["VALIO_REVIEW"] if kind == "VALIO" else [], minutes=minutes, conditions=conditions,
                       replaces=quote.id, reason=reason, counter_offer=True)
    quotes.set_status(quote.id, "SUPERSEDED")
    audit.append("PROPOSAL_MODIFIED", who, {"buyer_id": buyer_id, "order_id": order.id, "changes": c, "note": d.note,
                                            "old_quote_id": quote.id, "new_quote_id": new.id,
                                            "diff": [f.model_dump(mode="json") for f in diff]},
                 request_id=order.request_id,
                 summary=f"Approver changed the proposal ({', '.join(sorted(c))}): {d.note}. New quote {new.id}")

    price_moved = any(n.unit_price_cents != lines[n.product_id].unit_price_cents for n in new_lines)
    return new, kind == "BUYER" and not price_moved
