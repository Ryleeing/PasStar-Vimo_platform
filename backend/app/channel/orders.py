"""Orders (OrdersApi): idempotent placement → recheck the quote → gate → PLACED / pending approval /
buyer authorization required / requote required / rejected. After an approval or an external authorization the
order runs again from "recheck the quote", with the levels that were approved waived.

The orders table keeps only id, quote, status and gate. Everything else on an Order is derived:
  - BUYER_AUTHORIZATION_REQUIRED is a PENDING_BUYER_APPROVAL order under a protocol with external approval;
    its authorization_request is rebuilt from the quote, the protocol and the gate decision;
  - approval_id is the order's pending approval; authorized_by comes from the approval row that records an
    accepted external authorization;
  - new_quote_id / requote_reason come from the quote that replaced the order's quote."""
from __future__ import annotations

import json
from typing import Optional

from .. import db
from ..errors import ApiError
from ..internal import Audience
from ..schemas import GateDecision, Order, OrderSummary, PlacedBy, Protocol, Quote, StructuredRequest
from . import approvals, audit, checkpoints, gate, protocols, quotes, requests_view
from .authorization import AuthorizationError, build_request, resolve_buyer_gate, verify as verify_authorization
from .identity import actor

_LEVEL = {"BUYER": "BUYER_APPROVAL", "VALIO": "VALIO_REVIEW"}
_QUEUE = {"PENDING_BUYER_APPROVAL": "BUYER", "PENDING_VALIO_REVIEW": "VALIO"}


def _row(order_id: str):
    r = db.one("SELECT * FROM orders WHERE id = ?", (order_id,))
    if r is None:
        raise ApiError("NOT_FOUND", f"Order {order_id} does not exist")
    return r


def _load(r) -> Order:
    quote, decision = quotes.get(r["quote_id"]), GateDecision.model_validate_json(r["gate_json"])
    body = protocols.get(quote.protocol_id).body
    pending = db.one("SELECT id FROM approvals WHERE order_id = ? AND status = 'PENDING'", (r["id"],))
    external = db.one("SELECT decided_by FROM approvals WHERE order_id = ? AND note = ?", (r["id"], approvals.EXTERNAL))
    o = Order(id=r["id"], request_id=r["request_id"], quote_id=r["quote_id"], status=r["status"], gate=decision,
              approval_id=pending and pending["id"], authorized_by=external and external["decided_by"],
              created_at=r["created_at"], placed_at=r["placed_at"])
    if o.status == "PENDING_BUYER_APPROVAL" and body.approval_channel == "EXTERNAL":
        blocking = [h for h in decision.rules_hit if h.outcome == "BUYER_APPROVAL"]
        o.status = "BUYER_AUTHORIZATION_REQUIRED"
        o.authorization_request = build_request(quote, quote.protocol_id, body, [h.rule_id for h in blocking],
                                                [h.description for h in blocking])
    if o.status == "REQUOTE_REQUIRED":
        o.new_quote_id, o.requote_reason = quotes.replacement_of(o.quote_id)
    return o


def get(order_id: str, audience: Optional[Audience] = None) -> Order:
    r = _row(order_id)
    if audience and audience.kind != "VALIO_USER" and audience.buyer_id != quotes.buyer_of(r["quote_id"]):
        raise ApiError("NOT_FOUND", f"Order {order_id} does not exist")
    return _load(r)


def list_for(audience: Audience, limit: int = 50) -> list[OrderSummary]:
    """Every order of the caller's buyer, newest first, whoever placed it: a person on the screens or an agent
    (ChatGPT, Claude, the ERP). Valio sees every buyer's orders. Agents read single orders by id instead."""
    if audience.kind == "AGENT":
        raise ApiError("OUT_OF_SCOPE", "Agents read their orders one by one (GET /v1/orders/{order_id})")
    sql = ("SELECT o.id, r.buyer_id, r.created_by, r.structured_json FROM orders o JOIN requests r ON r.id = o.request_id"
           + ("" if audience.kind == "VALIO_USER" else " WHERE r.buyer_id = ?") + " ORDER BY o.created_at DESC, o.id DESC LIMIT ?")
    rows = db.all(sql, ((limit,) if audience.kind == "VALIO_USER" else (audience.buyer_id, limit)))
    out = []
    for r in rows:
        order = _load(_row(r["id"]))
        quote = quotes.get(order.quote_id)
        line = quote.lines[0]
        structured = StructuredRequest.model_validate_json(r["structured_json"])
        out.append(OrderSummary(order=order, buyer_id=r["buyer_id"], product=line.name, brand=line.brand,
                                quantity=structured.quantity, total_gross_cents=quote.totals.gross_cents,
                                delivery_date=quote.delivery_date, placed_by=_placed_by(r["created_by"])))
    return out


def _placed_by(created_by: str) -> PlacedBy:
    if created_by.startswith("agent:"):
        agent_id = created_by[len("agent:"):]
        a = db.one("SELECT a.name, c.kind FROM agents a LEFT JOIN agent_connections c ON c.agent_id = a.id WHERE a.id = ?",
                   (agent_id,))
        return PlacedBy(kind="AGENT", id=agent_id, name=a["name"] if a else agent_id, agent_kind=a["kind"] if a else None)
    u = db.one("SELECT name FROM users WHERE id = ?", (created_by,))
    return PlacedBy(kind="PERSON", id=created_by, name=u["name"] if u else created_by)


def _waived(order_id: str, quote_id: str) -> set[str]:
    """Levels already approved for this order's current quote. Approvals given before the proposal was changed
    (i.e. before the current quote existed) do not count."""
    rows = db.all("SELECT kind FROM approvals WHERE order_id = ? AND status IN ('APPROVED', 'APPROVED_WITH_CHANGES')"
                  " AND decided_at >= ?", (order_id, quotes.created_at(quote_id)))
    return {_LEVEL[r["kind"]] for r in rows} | set(quotes.internal(quote_id)["waived_levels"])


def _accept_authorization(order_id: str, token: str, quote: Quote, protocol: Protocol, who: str) -> None:
    """External authorization token: 403 AUTHORIZATION_INVALID when it does not check out. An accepted token is
    recorded as an approved buyer approval, decided by the approver named in the token."""
    agent_key = db.one("SELECT public_key FROM agents WHERE id = ?", (protocol.body.agent_id,))["public_key"]
    try:
        claims = verify_authorization(token, quote, protocol.id, protocol.body, agent_key and json.loads(agent_key))
    except AuthorizationError as e:
        audit.append("AUTHORIZATION_REJECTED", who, {"buyer_id": protocol.body.buyer_id, "quote_id": quote.id, "reason": e.reason},
                     request_id=quote.request_id, summary=f"Buyer authorization for {quote.id} refused: {e.reason}")
        raise ApiError("AUTHORIZATION_INVALID", e.message, reason=e.reason, quote_id=quote.id)
    approvals.record_external(order_id, claims.approver)
    audit.append("AUTHORIZATION_ACCEPTED", who, {"buyer_id": protocol.body.buyer_id, "quote_id": quote.id,
                                                 "approver": claims.approver, "amount_gross_cents": claims.amount_gross_cents},
                 request_id=quote.request_id,
                 summary=f"{claims.approver} authorized quote {quote.id} in the buyer's own system")


def _advance(order_id: str, quote: Quote, protocol: Protocol, who: str) -> dict:
    """Recheck → gate → order status. Returns the columns to write to the orders table."""
    req = requests_view.get(quote.request_id)
    rules = protocols.effective_rules(protocol, req.profile)
    body = protocol.body

    unchanged, new = quotes.recheck(quote)
    if not unchanged:
        decision = gate.preview(new.lines, new.totals.gross_cents, rules, quotes.internal(new.id)["data_unverified"])
        _held(who, body, order_id, quote, "REQUOTE_REQUIRED", ["QUOTE_CHANGED"],
              f"the quote changed; new quote {new.id}", new_quote_id=new.id)
        return {"quote_id": quote.id, "status": "REQUOTE_REQUIRED", "gate_json": decision.model_dump_json(), "placed_at": None}

    decision = gate.evaluate(quote, rules)
    waived = _waived(order_id, quote.id)
    open_hits = [h for h in decision.rules_hit if h.outcome not in waived]
    status = resolve_buyer_gate(gate.strictest(open_hits), "HOSTED", False)       # external approval is derived on read
    fields = {"quote_id": quote.id, "status": status, "gate_json": decision.model_dump_json(), "placed_at": None}
    if status == "PLACED":
        fields["placed_at"] = db.now().isoformat()
        quotes.set_status(quote.id, "USED")
    elif status == "PENDING_VALIO_REVIEW" or (status == "PENDING_BUYER_APPROVAL" and body.approval_channel == "HOSTED"):
        approvals.create(_QUEUE[status], "ORDER", [h.rule_id for h in open_hits if h.outcome == _LEVEL[_QUEUE[status]]],
                         body.buyer_id, order_id=order_id)
    shown = "BUYER_AUTHORIZATION_REQUIRED" if status == "PENDING_BUYER_APPROVAL" and body.approval_channel == "EXTERNAL" else status
    if status == "PLACED":
        audit.append("ORDER_PLACED", who, {"buyer_id": body.buyer_id, "order_id": order_id, "quote_id": quote.id,
                                           "waived": sorted(waived), "rules": [h.rule_id for h in decision.rules_hit]},
                     request_id=quote.request_id, summary=f"Order {order_id} is PLACED: {decision.summary}")
    else:
        blocking = [h for h in open_hits if h.outcome == gate.strictest(open_hits)]
        _held(who, body, order_id, quote, shown, [h.rule_id for h in blocking + open_hits if h.outcome != "AUTO"],
              blocking[0].description, waived=sorted(waived))
    return fields


_HELD_FOR = {"PENDING_BUYER_APPROVAL": "Held for approval", "BUYER_AUTHORIZATION_REQUIRED": "Held for buyer authorization",
             "PENDING_VALIO_REVIEW": "Held for Valio review", "REQUOTE_REQUIRED": "Held for a new quote",
             "REJECTED": "Refused"}


def _held(who: str, body, order_id: str, quote: Quote, status: str, reason_codes: list[str], why: str, **extra) -> None:
    """One ORDER_HELD entry for an order that was not placed straight away. reason_codes: the strictest rule first."""
    audit.append("ORDER_HELD", who, {"all_reason_codes": list(dict.fromkeys(reason_codes)), "order_id": order_id,
                                     "quote_id": quote.id, "status": status, "gross_cents": quote.totals.gross_cents, **extra},
                 request_id=quote.request_id, summary=f"{_HELD_FOR[status]}: {why}",
                 buyer_id=body.buyer_id, agent_id=body.agent_id, reason_code=reason_codes[0])


def _update(order_id: str, fields: dict) -> None:
    db.run(f"UPDATE orders SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), order_id))


# ───── OrdersApi ─────

def place(quote_id: str, protocol_id: str, idempotency_key: str, audience: Audience) -> Order:
    return submit(quote_id, protocol_id, idempotency_key, audience, None)


def submit(quote_id: str, protocol_id: str, idempotency_key: str, audience: Audience,
           buyer_authorization: Optional[str]) -> Order:
    """place() plus the optional external authorization token that POST /v1/orders may carry.
    Idempotency keys are per buyer: the column is unique across all buyers, so the key is stored with the buyer as
    prefix, and another buyer's key neither returns that buyer's order nor blocks the caller."""
    idempotency_key = f"{audience.buyer_id}:{idempotency_key}"
    existing = db.one("SELECT id FROM orders WHERE idempotency_key = ?", (idempotency_key,))
    if existing:
        return get(existing["id"])                       # same key submitted again returns the same order
    protocol = protocols.get_active(protocol_id, audience.agent_id)
    if audience.buyer_id != protocol.body.buyer_id:
        raise ApiError("OUT_OF_SCOPE", "This protocol belongs to another buyer")
    quote = quotes.get(quote_id)
    checkpoints.check_submit(quote, protocol)

    order_id, who = db.new_id("ord"), actor(audience)
    if buyer_authorization:
        _accept_authorization(order_id, buyer_authorization, quote, protocol, who)
    f = _advance(order_id, quote, protocol, who)
    db.run("INSERT INTO orders (id, request_id, quote_id, idempotency_key, status, gate_json, created_at, placed_at)"
           " VALUES (?,?,?,?,?,?,?,?)",
           (order_id, quote.request_id, quote.id, idempotency_key, f["status"], f["gate_json"], db.now().isoformat(), f["placed_at"]))
    return get(order_id)


def resume(order_id: str, who: str, quote_id: Optional[str] = None) -> Order:
    """Continue after an approval. quote_id: the new quote when an approver changed the proposal."""
    r = _row(order_id)
    quote = quotes.get(quote_id or r["quote_id"])
    _update(order_id, _advance(order_id, quote, protocols.get_active(quote.protocol_id, None), who))
    return get(order_id)


def authorize(order_id: str, token: str, audience: Audience) -> Order:
    """External approval: the agent comes back with the token signed by the buyer system; the same order continues."""
    o = get(order_id, audience)
    if o.status != "BUYER_AUTHORIZATION_REQUIRED":
        raise ApiError("VALIDATION_ERROR", f"Order {order_id} is {o.status}; it is not waiting for a buyer authorization")
    quote = quotes.get(o.quote_id)
    protocol = protocols.get_active(quote.protocol_id, audience.agent_id)
    _accept_authorization(order_id, token, quote, protocol, actor(audience))
    return resume(order_id, actor(audience))


def reject(order_id: str, who: str, reason: Optional[str]) -> Order:
    r = _row(order_id)
    _update(order_id, {"status": "REJECTED"})
    audit.append("ORDER_REJECTED", who, {"buyer_id": quotes.buyer_of(r["quote_id"]), "order_id": order_id, "reason": reason},
                 request_id=r["request_id"], summary=f"Order {order_id} rejected by the approver")
    return get(order_id)


def requote(order_id: str) -> Order:
    """The order's quote was replaced (a Valio counter-offer): the order stops at REQUOTE_REQUIRED and the buyer
    confirms by ordering again with the new quote."""
    _update(order_id, {"status": "REQUOTE_REQUIRED"})
    return get(order_id)
