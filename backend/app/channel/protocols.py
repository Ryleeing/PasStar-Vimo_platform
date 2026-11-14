"""Authorization protocols (ProtocolsApi + protocol endpoints): draft, edit, sign, Valio confirmation, amend, revoke.
Iron rule: an agent may draft a protocol, but can neither activate it nor widen its own permissions;
permissions come only from a signed protocol version."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from .. import db, deps
from ..errors import ApiError
from ..internal import EffectiveRules
from ..schemas import (Profile, Protocol, ProtocolBody, ProtocolDiff, ProtocolDraftRequest, RankingWeights,
                       SubstitutionPolicy)
from . import approvals, audit, signing
from .identity import Caller, actor
from .protocol_rules import apply_tightening, classify_changes


def _load(r) -> Protocol:
    return Protocol(id=r["id"], version=r["version"], status=r["status"], body=json.loads(r["body_json"]),
                    open_questions=json.loads(r["open_questions_json"]), jws=r["jws"], supersedes=r["supersedes"],
                    created_at=r["created_at"], buyer_signed_at=r["buyer_signed_at"],
                    valio_confirmed_at=r["valio_confirmed_at"])


def insert(p: Protocol) -> None:
    db.run("INSERT INTO protocols (id, buyer_id, agent_id, version, status, body_json, open_questions_json, jws,"
           " supersedes, created_at, buyer_signed_at, valio_confirmed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
           (p.id, p.body.buyer_id, p.body.agent_id, p.version, p.status, p.body.model_dump_json(),
            json.dumps([q.model_dump() for q in p.open_questions]), p.jws, p.supersedes, p.created_at.isoformat(),
            p.buyer_signed_at and p.buyer_signed_at.isoformat(), p.valio_confirmed_at and p.valio_confirmed_at.isoformat()))


def jws_for(protocol_id: str, version: int, body: ProtocolBody) -> str:
    return signing.sign({"protocol_id": protocol_id, "version": version, "body": body.model_dump(mode="json")}, "protocol+jws")


def get(protocol_id: str) -> Protocol:
    r = db.one("SELECT * FROM protocols WHERE id = ?", (protocol_id,))
    if r is None:
        raise ApiError("NOT_FOUND", f"Protocol {protocol_id} does not exist")
    return _load(r)


def get_for(protocol_id: str, who: Caller) -> Protocol:
    """The buyer side can only see its own protocols."""
    p = get(protocol_id)
    if who.kind != "VALIO_USER" and who.buyer_id != p.body.buyer_id:
        raise ApiError("NOT_FOUND", f"Protocol {protocol_id} does not exist")
    return p


def _expect(p: Protocol, *statuses: str) -> None:
    if p.status not in statuses:
        raise ApiError("PROTOCOL_INVALID", f"Protocol {p.id} is {p.status}; expected {' or '.join(statuses)}")


def _log(event: str, p: Protocol, who: Caller, summary: str, **payload) -> None:
    audit.append(event, actor(who), {"buyer_id": p.body.buyer_id, "protocol_id": p.id, "version": p.version, **payload},
                 summary=summary)


# ───── ProtocolsApi (used by BE3 and inside BE1) ─────

def get_active(protocol_id: str, agent_id: Optional[str]) -> Protocol:
    p = get(protocol_id)
    if p.status == "ACTIVE" and p.body.valid_until < db.now().date():
        db.run("UPDATE protocols SET status = 'EXPIRED' WHERE id = ?", (p.id,))
        p.status = "EXPIRED"
    _expect(p, "ACTIVE")
    if agent_id is not None and agent_id != p.body.agent_id:
        raise ApiError("PROTOCOL_INVALID", f"Agent {agent_id} is not the agent named in protocol {p.id}")
    return p


def active_for_agent(agent_id: str, who: Caller) -> Protocol:
    """The protocol an agent acts under now: its latest ACTIVE version. A raised limit creates a new version, so
    callers look the protocol up through the agent instead of keeping a fixed protocol id."""
    a = db.one("SELECT buyer_id FROM agents WHERE id = ?", (agent_id,))
    visible = a is not None and (who.kind == "VALIO_USER" or (who.kind == "AGENT" and who.agent_id == agent_id)
                                 or (who.kind == "BUYER_USER" and who.buyer_id == a["buyer_id"]))
    if not visible:
        raise ApiError("NOT_FOUND", f"Agent {agent_id} does not exist")
    r = db.one("SELECT id FROM protocols WHERE agent_id = ? AND status = 'ACTIVE' ORDER BY version DESC LIMIT 1",
               (agent_id,))
    if r is None:
        raise ApiError("PROTOCOL_INVALID", f"Agent {agent_id} has no active protocol; the buyer must sign one first")
    return get_active(r["id"], None)


def effective_rules(protocol: Protocol, profile: Optional[Profile]) -> EffectiveRules:
    """Protocol ∩ profile: hard requirements are unioned, substitution takes the stricter side.
    A profile can never make anything wider than the protocol."""
    body = protocol.body
    if profile is None:
        return EffectiveRules(protocol=body, hard_requirements=list(body.hard_requirements), excluded_brands=[],
                              excluded_vendors=[], excluded_product_ids=[], auto_limit_cents=body.auto_approve_limit_cents,
                              max_cents=body.max_order_cents, weights=RankingWeights().model_dump(), soft={})
    pb = profile.body
    have = {(r.kind, r.code) for r in body.hard_requirements}
    reqs = body.hard_requirements + [r for r in pb.hard_exclusions.requirements if (r.kind, r.code) not in have]
    sub = body.substitution
    if pb.defaults.substitution == "NONE":
        sub = SubstitutionPolicy(official="FORBIDDEN", same_category="FORBIDDEN", cross_category="FORBIDDEN")
    elif pb.defaults.substitution == "SAME_CATEGORY":
        sub = sub.model_copy(update={"cross_category": "FORBIDDEN"})
    return EffectiveRules(
        protocol=body.model_copy(update={"hard_requirements": reqs, "substitution": sub}), hard_requirements=reqs,
        excluded_brands=pb.hard_exclusions.brands, excluded_vendors=pb.hard_exclusions.vendors,
        excluded_product_ids=pb.hard_exclusions.product_ids, auto_limit_cents=body.auto_approve_limit_cents,
        max_cents=body.max_order_cents, weights=pb.weights.model_dump(), soft=pb.soft_preferences.model_dump())


# ───── Protocol endpoints ─────

def draft(req: ProtocolDraftRequest, who: Caller) -> Protocol:
    if who.buyer_id != req.buyer_id:
        raise ApiError("OUT_OF_SCOPE", "A protocol can only be drafted for your own buyer")
    body, questions = deps.parser.draft_protocol(req.buyer_id, req.agent_id, req.description)
    p = Protocol(id=db.new_id("prot"), version=1, status="DRAFT", body=body, open_questions=questions, created_at=db.now())
    insert(p)
    _log("PROTOCOL_DRAFTED", p, who, f"Protocol {p.id} drafted from: {req.description}")
    return p


def update(protocol_id: str, body: ProtocolBody, who: Caller) -> Protocol:
    p = get_for(protocol_id, who)
    _expect(p, "DRAFT")
    if (body.buyer_id, body.agent_id) != (p.body.buyer_id, p.body.agent_id):
        raise ApiError("OUT_OF_SCOPE", "buyer_id and agent_id cannot be changed; draft a new protocol instead")
    db.run("UPDATE protocols SET body_json = ? WHERE id = ?", (body.model_dump_json(), p.id))
    _log("PROTOCOL_UPDATED", p, who, f"Draft {p.id} edited")
    return get(p.id)


def buyer_sign(protocol_id: str, who: Caller) -> Protocol:
    """The approver named in the protocol signs → PENDING_VALIO_CONFIRMATION. Expanded versions are signed here too."""
    p = get_for(protocol_id, who)
    _expect(p, "DRAFT", "PENDING_BUYER_SIGNATURE")
    approver = signing_body(p).approver
    if who.user_id != approver:
        raise ApiError("NOT_APPROVER", "Only the approver named in the protocol can sign it", approver=approver)
    at = db.now().isoformat()
    db.run("UPDATE protocols SET status = 'PENDING_VALIO_CONFIRMATION', buyer_signed_at = ? WHERE id = ?", (at, p.id))
    db.run("UPDATE approvals SET status = 'APPROVED', decided_by = ?, decided_at = ?"
           " WHERE protocol_id = ? AND status = 'PENDING'", (who.user_id, at, p.id))
    _log("PROTOCOL_SIGNED", p, who, f"{who.user_id} signed protocol {p.id}")
    return get(p.id)


def signing_body(p: Protocol) -> ProtocolBody:
    """Whose signature a protocol needs: a new version is signed under the version it replaces, so an expansion
    that names a new approver is still signed by the current one."""
    return (get(p.supersedes) if p.status == "PENDING_BUYER_SIGNATURE" else p).body


def confirm(protocol_id: str, who: Caller) -> Protocol:
    """Valio confirms → ACTIVE, signed by Valio; the version it replaces → SUPERSEDED."""
    if who.kind != "VALIO_USER":
        raise ApiError("NOT_APPROVER", "Only Valio can confirm a protocol")
    p = get(protocol_id)
    _expect(p, "PENDING_VALIO_CONFIRMATION")
    db.run("UPDATE protocols SET status = 'ACTIVE', valio_confirmed_at = ?, jws = ? WHERE id = ?",
           (db.now().isoformat(), jws_for(p.id, p.version, p.body), p.id))
    if p.supersedes:
        db.run("UPDATE protocols SET status = 'SUPERSEDED' WHERE id = ?", (p.supersedes,))
    _log("PROTOCOL_CONFIRMED", p, who, f"Valio confirmed protocol {p.id}; it is now ACTIVE", supersedes=p.supersedes)
    return get(p.id)


def amend(protocol_id: str, changes: dict[str, Any], who: Caller) -> ProtocolDiff:
    """Tightening takes effect at once (re-signed); expansion creates a new version that waits for the approver to
    sign; changes the caller may not make are dropped and listed."""
    p = get_for(protocol_id, who)
    _expect(p, "ACTIVE")
    result = classify_changes(p.body, changes, "valio" if who.kind == "VALIO_USER" else "buyer", db.now().date())
    diff = ProtocolDiff(**result, result_status="ACTIVE")

    tightened = apply_tightening(p.body, changes, result)
    if tightened != p.body:
        db.run("UPDATE protocols SET body_json = ?, jws = ? WHERE id = ?",
               (tightened.model_dump_json(), jws_for(p.id, p.version, tightened), p.id))
        _log("PROTOCOL_TIGHTENED", p, who, f"Protocol {p.id} tightened; effective immediately",
             changes=[c.model_dump() for c in diff.tightened])

    if diff.expanded:
        refused = {c.field.split(".")[0] for c in diff.forbidden}
        body = tightened.model_dump(mode="json")
        for field, new in changes.items():
            if field not in refused:
                body[field] = {**body[field], **new} if isinstance(body[field], dict) else new
        nxt = Protocol(id=re.sub(r"_v\d+$", "", p.id) + f"_v{p.version + 1}", version=p.version + 1,
                       status="PENDING_BUYER_SIGNATURE", body=body, supersedes=p.id, created_at=db.now())
        insert(nxt)
        diff.result_status = nxt.status
        diff.approval_id = approvals.create("BUYER", "PROTOCOL_CHANGE", ["PROTOCOL_EXPANSION"], p.body.buyer_id,
                                            protocol_id=nxt.id)
        _log("PROTOCOL_EXPANSION_REQUESTED", nxt, who, f"Expansion of {p.id} waits for {p.body.approver} to sign {nxt.id}",
             changes=[c.model_dump() for c in diff.expanded], approval_id=diff.approval_id)

    if diff.forbidden:
        _log("PROTOCOL_CHANGE_REFUSED", p, who, f"Refused changes to {p.id} that the caller may not make",
             changes=[c.model_dump() for c in diff.forbidden])
    return diff


def expansion_diff(new: Protocol) -> ProtocolDiff:
    """What a pending new version adds over the version it replaces (shown on the approval card)."""
    old, body = get(new.supersedes).body, new.body.model_dump(mode="json")
    current = old.model_dump(mode="json")
    changed = {k: v for k, v in body.items() if v != current[k]}
    result = classify_changes(old, changed, "valio", new.created_at.date())    # forbidden parts were dropped at amend time
    return ProtocolDiff(tightened=result["tightened"], expanded=result["expanded"], result_status=new.status)


def revoke(protocol_id: str, who: Caller) -> Protocol:
    p = get_for(protocol_id, who)
    if who.kind != "VALIO_USER" and who.user_id != p.body.approver:
        raise ApiError("NOT_APPROVER", "Only the approver named in the protocol or Valio can revoke it")
    db.run("UPDATE protocols SET status = 'REVOKED' WHERE id = ?", (p.id,))
    _log("PROTOCOL_REVOKED", p, who, f"Protocol {p.id} revoked")
    return get(p.id)
