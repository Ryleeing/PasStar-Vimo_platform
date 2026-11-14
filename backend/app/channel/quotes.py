"""Signed quotes (QuotesApi): products, quantities, prices and delivery date are locked by a Valio signature for
15 minutes and rechecked before ordering. Prices and stock come only through the ValioApi provided by BE2.

quotes.body_json holds {"signed": ..., "internal": ...}:
  signed   – exactly what the JWS covers: the Quote fields, plus `conditions` and `counter_offer_of` when an
             approver changed the proposal (anyone can read them by decoding the JWS);
  internal – what the channel needs later and never sends out: data_unverified, price_overrides, waived_levels,
             replaces (the quote this one replaced) and reason (why it was replaced)."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Optional

from joserfc.errors import JoseError

from .. import db, deps
from ..errors import ApiError
from ..schemas import Candidate, CandidateLine, Protocol, Quote, QuoteVerifyResult, Totals
from . import audit, protocols, signing

VALID_MINUTES = 15


def price(lines: list[CandidateLine], protocol: Protocol, delivery_date: date,
          overrides: dict[str, int]) -> tuple[list[CandidateLine], Totals]:
    """Ask Valio for the current price and stock of these lines. overrides are unit prices set in a Valio counter-offer."""
    ids = [l.product_id for l in lines]
    prices = deps.valio.prices(ids, protocol.body.buyer_id)
    stock = deps.valio.availability(ids, delivery_date, {l.product_id: l.sales_units for l in lines}, protocol.body.agent_id)
    out = []
    for l in lines:
        p, s = prices[l.product_id], stock[l.product_id]
        unit = overrides.get(l.product_id, p.unit_price_cents)
        out.append(l.model_copy(update={
            "unit_price_cents": unit, "line_net_cents": round(unit * l.sales_units), "vat_rate": p.vat_rate,
            "deposit_cents": round(p.deposit_cents_per_unit * l.sales_units), "available_on_date": s.status,
            # tell the buyer how much can be delivered only when it is PARTIAL (BE2 caps it at the requested amount)
            "deliverable_sales_units": s.deliverable_sales_units if s.status == "PARTIAL" else None}))
    net = sum(l.line_net_cents for l in out)
    vat = sum(round(l.line_net_cents * l.vat_rate) for l in out)
    deposit = sum(l.deposit_cents for l in out)
    return out, Totals(net_cents=net, vat_cents=vat, deposit_cents=deposit, gross_cents=net + vat + deposit)


def issue(request_id: str, candidate_id: str, protocol: Protocol, lines: list[CandidateLine], totals: Totals,
          delivery_date: date, data_unverified: bool, price_overrides: Optional[dict[str, int]] = None, waived_levels: list[str] = (),
          minutes: int = VALID_MINUTES, conditions: list[str] = (), replaces: Optional[str] = None,
          reason: Optional[str] = None, counter_offer: bool = False) -> Quote:
    """Sign and store a quote."""
    now = db.now()
    q = Quote(id=db.new_id("quo"), request_id=request_id, candidate_id=candidate_id, protocol_id=protocol.id,
              lines=lines, totals=totals, delivery_date=delivery_date, valid_until=now + timedelta(minutes=minutes),
              status="VALID", jws="")
    signed = q.model_dump(mode="json", exclude={"status", "jws"})
    if counter_offer:
        signed.update(conditions=list(conditions), counter_offer_of=replaces)
    internal = {"data_unverified": data_unverified, "price_overrides": price_overrides or {}, "waived_levels": list(waived_levels),
                "conditions": list(conditions), "replaces": replaces, "reason": reason}
    q.jws = signing.sign(signed, "quote+jws")
    db.run("INSERT INTO quotes (id, request_id, candidate_id, body_json, jws, valid_until, status, created_at)"
           " VALUES (?,?,?,?,?,?,?,?)",
           (q.id, request_id, candidate_id, json.dumps({"signed": signed, "internal": internal}), q.jws,
            q.valid_until.isoformat(), q.status, now.isoformat()))
    audit.append("QUOTE_CREATED", "system", {"buyer_id": protocol.body.buyer_id, "quote_id": q.id, "candidate_id": candidate_id,
                                             "totals": totals.model_dump(), "valid_until": q.valid_until, "replaces": replaces},
                 request_id=request_id,
                 summary=f"Signed quote {q.id}: {len(lines)} line(s), €{totals.gross_cents / 100:,.2f}, valid {minutes} min")
    return q


def unverified(candidate: Candidate) -> bool:
    """Only "explicitly satisfied + grade A" counts as satisfied; everything else is unverified."""
    return any(r.status not in ("FREE_FROM", "CLAIMED") or r.trust_grade != "A" for r in candidate.cards.allergen.rows)


def _row(quote_id: str):
    r = db.one("SELECT * FROM quotes WHERE id = ?", (quote_id,))
    if r is None:
        raise ApiError("NOT_FOUND", f"Quote {quote_id} does not exist")
    return r


def get(quote_id: str) -> Quote:
    r = _row(quote_id)
    q = Quote(**json.loads(r["body_json"])["signed"], status=r["status"], jws=r["jws"])
    if q.status == "VALID" and db.now() > q.valid_until:
        set_status(q.id, "EXPIRED")
        q.status = "EXPIRED"
    return q


def internal(quote_id: str) -> dict:
    return json.loads(_row(quote_id)["body_json"])["internal"]


def created_at(quote_id: str) -> str:
    return _row(quote_id)["created_at"]


def buyer_of(quote_id: str) -> str:
    return protocols.get(get(quote_id).protocol_id).body.buyer_id


def replacement_of(quote_id: str) -> tuple[str, str]:
    """(id of the quote that replaced this one, why)."""
    r = db.one("SELECT id, body_json FROM quotes WHERE json_extract(body_json, '$.internal.replaces') = ?"
               " ORDER BY created_at DESC", (quote_id,))
    return r["id"], json.loads(r["body_json"])["internal"]["reason"]


def set_status(quote_id: str, status: str) -> None:
    db.run("UPDATE quotes SET status = ? WHERE id = ?", (status, quote_id))


# ───── QuotesApi ─────

def create(request_id: str, candidate: Candidate, protocol: Protocol) -> Quote:
    """Called when a candidate is selected: re-query price and stock, sign, valid for 15 minutes.
    Blocked candidates cannot be quoted."""
    if not candidate.selectable:
        raise ApiError("OUT_OF_SCOPE", "This candidate violates a hard requirement and cannot be selected",
                       violations=[v.model_dump() for v in candidate.violations])
    lines, totals = price(candidate.lines, protocol, candidate.delivery_date, {})
    return issue(request_id, candidate.id, protocol, lines, totals, candidate.delivery_date, unverified(candidate))


def recheck(quote: Quote) -> tuple[bool, Optional[Quote]]:
    """Recheck before ordering: unchanged → (True, None); if price or stock moved, the old quote is superseded
    and (False, new quote) is returned."""
    protocol, x = protocols.get(quote.protocol_id), internal(quote.id)
    lines, totals = price(quote.lines, protocol, quote.delivery_date, x["price_overrides"])
    if lines == quote.lines:
        return True, None
    new = issue(quote.request_id, quote.candidate_id, protocol, lines, totals, quote.delivery_date, x["data_unverified"],
                x["price_overrides"], x["waived_levels"], conditions=x["conditions"], replaces=quote.id,
                reason="Price or availability changed since the quote was issued", counter_offer=bool(x["conditions"]))
    set_status(quote.id, "SUPERSEDED")
    audit.append("QUOTE_CHANGED", "system", {"buyer_id": protocol.body.buyer_id, "old_quote_id": quote.id, "new_quote_id": new.id,
                                             "old_gross_cents": quote.totals.gross_cents, "new_gross_cents": totals.gross_cents},
                 request_id=quote.request_id,
                 summary=f"Price or availability changed since quote {quote.id}; replaced by {new.id}")
    return False, new


def verify(quote_id: str) -> QuoteVerifyResult:
    r = _row(quote_id)
    try:
        valid = signing.verify(r["jws"]) == json.loads(r["body_json"])["signed"]
    except JoseError:
        valid = False
    return QuoteVerifyResult(quote_id=quote_id, signature_valid=valid, expired=db.now() > datetime.fromisoformat(r["valid_until"]),
                             key_id=signing.KID)
