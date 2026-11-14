"""Approvals (BE1): two queues (buyer, Valio), approval cards, decisions.
The buyer approver decides on money and requirements; the Valio reviewer decides what may be sold and disclosed.
After an approval the order runs again from the quote recheck.
The approver rules are in the technical design, "protocol rules", under approval_mode.

The approvals table stores the decision; everything else on a card is derived from the order, its quote, the
protocol and the request."""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Literal, Optional

from .. import db
from ..errors import ApiError
from ..schemas import AgentActivity, ApprovalCard, ApprovalDecision, ApprovalDecisionResult, GateDecision, RiskTag
from . import audit, checkpoints, gate, orders, proposals, protocols, quotes, requests_view
from .identity import Caller, actor

ApproverError = Literal["SELF_APPROVAL_FORBIDDEN", "NOT_APPROVER"]
_MESSAGES = {"SELF_APPROVAL_FORBIDDEN": "In SEPARATE mode the requester cannot approve their own order",
             "NOT_APPROVER": "Only the approver named in the protocol can decide"}
EXTERNAL = "EXTERNAL_AUTHORIZATION"      # note on the approval row that records an accepted external authorization
# gate rule → risk tag
_RISK = {"R-SPEND-APPROVAL": ("SPEND", "MEDIUM"), "R-DELIVERY-PARTIAL": ("DELIVERY", "MEDIUM"),
         "R-DATA-UNVERIFIED": ("ALLERGEN", "HIGH"), "R-QTY-INEXACT": ("DATA", "MEDIUM"),
         "R-SUB-CROSS": ("SUBSTITUTION", "MEDIUM"), "R-SUB-SAME": ("SUBSTITUTION", "MEDIUM"),
         "R-SUB-OFFICIAL": ("SUBSTITUTION", "LOW"), "R-OUT-OF-API": ("DELIVERY", "HIGH")}


def check_approver(approval_mode: str, approver: str, requested_by: Optional[str], decider: str) -> Optional[ApproverError]:
    """None means the decider may approve; otherwise the error code (403 at the API layer).
    - SEPARATE: the requester cannot approve their own order; anyone else must be the approver in the protocol;
    - SELF: the approver in the protocol may approve, including their own request (logged as self-approved)."""
    if approval_mode == "SEPARATE" and requested_by is not None and decider == requested_by:
        return "SELF_APPROVAL_FORBIDDEN"
    if decider != approver:
        return "NOT_APPROVER"
    return None


def is_self_approval(requested_by: Optional[str], decider: str) -> bool:
    """For the audit log: an approver deciding on their own request is recorded as ORDER_SELF_APPROVED."""
    return requested_by is not None and requested_by == decider


def create(kind: str, subject: str, reason_codes: list[str], buyer_id: str,
           order_id: Optional[str] = None, protocol_id: Optional[str] = None) -> str:
    approval_id = db.new_id("apr")
    db.run("INSERT INTO approvals (id, order_id, protocol_id, kind, subject, reason_codes_json, status, created_at)"
           " VALUES (?,?,?,?,?,?,'PENDING',?)",
           (approval_id, order_id, protocol_id, kind, subject, json.dumps(reason_codes), db.now().isoformat()))
    audit.append("APPROVAL_CREATED", "system", {"buyer_id": buyer_id, "approval_id": approval_id, "kind": kind, "subject": subject,
                                                "order_id": order_id, "protocol_id": protocol_id, "reason_codes": reason_codes},
                 summary=f"{kind} approval {approval_id} opened for {subject}: {', '.join(reason_codes)}")
    return approval_id


def record_external(order_id: str, approver: str) -> None:
    """An authorization signed in the buyer's own system counts as an approved buyer approval."""
    at = db.now().isoformat()
    db.run("INSERT INTO approvals (id, order_id, kind, subject, reason_codes_json, status, decided_by, decided_at, note,"
           " created_at) VALUES (?,?,'BUYER','ORDER','[]','APPROVED',?,?,?,?)",
           (db.new_id("apr"), order_id, approver, at, EXTERNAL, at))


def _protocol_of(r):
    """The protocol an approval falls under: the one being changed, or the one of the order's quote."""
    if r["protocol_id"]:
        return protocols.get(r["protocol_id"])
    quote_id = db.one("SELECT quote_id FROM orders WHERE id = ?", (r["order_id"],))["quote_id"]
    return protocols.get(quotes.get(quote_id).protocol_id)


def _visible(r, who: Caller) -> bool:
    """The buyer side sees only its own buyer approvals; Valio sees everything."""
    return who.kind == "VALIO_USER" or (r["kind"] == "BUYER" and _protocol_of(r).body.buyer_id == who.buyer_id)


def _row(approval_id: str, who: Caller):
    r = db.one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
    if r is None or not _visible(r, who):
        raise ApiError("NOT_FOUND", f"Approval {approval_id} does not exist")
    return r


def _activity(agent_id: str) -> AgentActivity:
    since = (db.now() - timedelta(hours=24)).isoformat()
    checkpoints.flush_blocks()
    # one AGENT_BLOCKED entry stands for itself plus the blocks it suppressed
    count = lambda n, extra: db.one(f"SELECT COALESCE(SUM({n}), 0) AS n FROM audit_log WHERE actor = ? AND at >= ? {extra}",
                                    (f"agent:{agent_id}", since))["n"]
    blocks = "1 + json_extract(payload_json, '$.payload.suppressed')"
    return AgentActivity(agent_id=agent_id, name=db.one("SELECT name FROM agents WHERE id = ?", (agent_id,))["name"],
                         requests_24h=count("1", "AND event != 'AGENT_BLOCKED'"),
                         blocked_24h=count(blocks, "AND event = 'AGENT_BLOCKED'"),
                         probe_alerts_24h=count(blocks, "AND event = 'AGENT_BLOCKED' AND reason_code = 'PROBE_LIMIT'"))


def card(r) -> ApprovalCard:
    codes, protocol = json.loads(r["reason_codes_json"]), _protocol_of(r)
    body = protocols.signing_body(protocol)
    common = dict(id=r["id"], kind=r["kind"], subject=r["subject"], status=r["status"], created_at=r["created_at"],
                  reason_codes=codes, approver=body.approver, approval_mode=body.approval_mode)

    if r["subject"] == "PROTOCOL_CHANGE":
        diff = protocols.expansion_diff(protocol)
        diff.approval_id = r["id"]
        return ApprovalCard(
            **common, title=f"Protocol change: {len(diff.expanded)} expansion(s) need a new signature",
            reasons=[f"{c.field}: {c.before} → {c.after}" for c in diff.expanded], protocol_diff=diff,
            suggested_action="APPROVE", suggested_reason="Expansions take effect only after the approver signs and Valio confirms")

    o = db.one("SELECT * FROM orders WHERE id = ?", (r["order_id"],))
    quote, req = quotes.get(o["quote_id"]), requests_view.get(o["request_id"])
    hits = [h for h in GateDecision.model_validate_json(o["gate_json"]).rules_hit if h.rule_id in codes]
    tags = [RiskTag(kind=_RISK[h.rule_id][0], level=_RISK[h.rule_id][1], text=h.description) for h in hits]
    risky, s = any(t.level == "HIGH" for t in tags), req.structured
    return ApprovalCard(
        **common, title=f"{s.quantity.value:,g} {s.quantity.unit} {s.category_label or s.category}, {gate.eur(quote.totals.gross_cents)}",
        reasons=[h.description for h in hits] or ["Authorized in the buyer's own system"], risk_tags=tags,
        request=s, raw_text=req.raw_text, requested_by=req.created_by, quote=quote,
        candidate=next((c for c in req.candidates if c.id == quote.candidate_id), None),
        alternatives=[c for c in req.candidates if c.id != quote.candidate_id][:3],
        agent_activity=_activity(body.agent_id) if r["kind"] == "VALIO" else None,
        suggested_action=("ROUTE" if r["kind"] == "VALIO" else "REJECT") if risky else "APPROVE",
        suggested_reason="High risk: " + "; ".join(t.text for t in tags if t.level == "HIGH") if risky
        else "Within the order maximum; no high-risk flags")


def list_cards(kind: Optional[str], status: Optional[str], who: Caller) -> list[ApprovalCard]:
    where = " AND ".join(f"{column} = ?" for column, value in (("kind", kind), ("status", status)) if value)
    rows = db.all("SELECT * FROM approvals" + (f" WHERE {where}" if where else "") + " ORDER BY created_at DESC",
                  tuple(v for v in (kind, status) if v))
    return [card(r) for r in rows if _visible(r, who)]


def get_card(approval_id: str, who: Caller) -> ApprovalCard:
    return card(_row(approval_id, who))


def decide(approval_id: str, d: ApprovalDecision, who: Caller) -> ApprovalDecisionResult:
    r = _row(approval_id, who)
    if r["status"] != "PENDING":
        raise ApiError("VALIDATION_ERROR", f"Approval {approval_id} is already {r['status']}")
    kind, subject, order_id, me = r["kind"], r["subject"], r["order_id"], actor(who)
    body = protocols.signing_body(_protocol_of(r))

    # who may decide: buyer approvals follow the approver in the protocol; Valio approvals need a Valio reviewer
    self_approved = False
    if kind == "BUYER":
        # a protocol change is re-signed by the approver themselves, so only orders have a requester to compare with
        requester = requests_view.get(orders.get(order_id).request_id).created_by if subject == "ORDER" else None
        code = check_approver(body.approval_mode, body.approver, requester, who.user_id)
        if code:
            raise ApiError(code, _MESSAGES[code], requested_by=requester, approver=body.approver)
        self_approved = is_self_approval(requester, who.user_id)
    elif who.kind != "VALIO_USER":
        raise ApiError("NOT_APPROVER", "Only Valio reviewers can decide this approval")

    def close(status: str) -> None:
        db.run("UPDATE approvals SET status = ?, decided_by = ?, decided_at = ?, note = ? WHERE id = ?",
               (status, who.user_id, db.now().isoformat(), d.note, approval_id))

    order = None
    if d.decision == "APPROVE":
        close("APPROVED")
        if subject == "ORDER":
            order = orders.resume(order_id, me)
        else:
            protocols.buyer_sign(r["protocol_id"], who)
    elif d.decision == "APPROVE_WITH_CHANGES":
        if not d.changes or not d.note:
            raise ApiError("VALIDATION_ERROR", "Changing a proposal needs both `changes` and a reason in `note`")
        if subject != "ORDER":
            raise ApiError("VALIDATION_ERROR", "A protocol change is signed or rejected as a whole; amend it again to change it")
        new_quote, proceed = proposals.modify(kind, orders.get(order_id), d, me)
        if new_quote is None:                                # beyond the reviewer's authority: escalated, nothing changed
            close("ROUTED")
        else:
            close("APPROVED_WITH_CHANGES")
            order = orders.resume(order_id, me, quote_id=new_quote.id) if proceed else orders.requote(order_id)
    elif d.decision == "REJECT":
        close("REJECTED")
        if subject == "ORDER":
            order = orders.reject(order_id, me, d.note)
        else:
            db.run("UPDATE protocols SET status = 'REVOKED' WHERE id = ?", (r["protocol_id"],))
    else:
        close("ROUTED")

    done = db.one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
    audit.append("ORDER_SELF_APPROVED" if self_approved and done["status"].startswith("APPROVED") else "APPROVAL_DECIDED", me,
                 {"buyer_id": body.buyer_id, "approval_id": approval_id, "decision": d.decision, "status": done["status"],
                  "order_id": order_id, "protocol_id": r["protocol_id"], "note": d.note, "route_to": d.route_to},
                 request_id=order and order.request_id,
                 summary=f"{who.user_id} decided {kind} approval {approval_id}: {done['status']}"
                         + (" (self-approved)" if self_approved else ""))
    return ApprovalDecisionResult(approval=card(done), order=order)
