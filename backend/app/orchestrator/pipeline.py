"""The four-node pipeline behind POST /v1/requests and node rejection.

Nodes: 1 UNDERSTAND -> 2 FILTER -> 3 RANK -> 4 GATE. Each run writes one attempt
to chain_nodes and candidates. Rejecting node N re-runs N..4 as attempt + 1; nodes
before N are copied unchanged. Each node can be rejected at most MAX_RETRIES times.
Constraints from rejections accumulate for the request and never touch the profile.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from ..errors import ApiError
from ..internal import (
    AuditApi, Audience, CardsApi, CatalogApi, DisclosureApi, EffectiveRules, ExplainApi, GateApi, ProductRecord,
    ProtocolsApi, UnitsApi, ValioApi,
)
from ..schemas import (
    Candidate, CandidateLine, ChainEvent, ChainNode, ChainTimeline, CreateRequest, EvidenceRef, GateDecision,
    RejectNodeRequest, RequestResult, Requirement, RuleHit, StructuredRequest, Totals, Violation,
)
from .holds import record_blocked
from .profiles import ProfileRepo
from .ranking import Draft, deliverable, rank

NODE_NAMES = {1: "UNDERSTAND", 2: "FILTER", 3: "RANK", 4: "GATE"}
REJECT_OPTIONS = {
    1: ["WRONG_QUANTITY", "WRONG_DATE", "WRONG_ATTRIBUTES", "WRONG_CATEGORY", "OTHER"],
    2: ["TOO_STRICT", "TOO_LOOSE", "ALLOW_BRAND", "EXCLUDE_PRODUCT", "OTHER"],
    3: ["WRONG_PRIORITY", "EXCLUDE_OPTION", "PREFER_SINGLE_SKU", "OTHER"],
    4: ["SEND_TO_APPROVAL", "PICK_OTHER"],   # can never make the gate more permissive
}
MAX_RETRIES = 3
MAX_CANDIDATES = 8
MAX_BLOCKED_SHOWN = 5
OUTCOME_ORDER = ["AUTO", "BUYER_APPROVAL", "VALIO_REVIEW", "REJECT"]
LLM = Audience(kind="LLM", buyer_id=None, agent_id=None)


# The shared error type (app/errors.py); main.py turns it into ErrorBody. Kept under this name for BE3 code.
PipelineError = ApiError


@dataclass
class Deps:
    catalog: CatalogApi
    units: UnitsApi
    valio: ValioApi
    cards: CardsApi
    disclosure: DisclosureApi
    protocols: ProtocolsApi
    gate: GateApi
    explain: ExplainApi
    audit: Optional[AuditApi] = None    # hold events (CANDIDATES_BLOCKED); skipped when not wired
    # product, requirement -> True when the requirement is not proven (R-DATA-UNVERIFIED). BE2's
    # requirement_check in live mode (orchestrator/be2.py); without it, only structured claims count.
    unverified: Optional[Callable[[ProductRecord, Requirement], bool]] = None


def reliability_level(value: Optional[float]) -> str:
    if value is None:
        return "UNKNOWN"
    return "HIGH" if value >= 0.98 else "MEDIUM" if value >= 0.9 else "LOW"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


TRUST_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}   # Reliable, Reliable?, Risky
AGENT_PREFIX = "agent:"   # created_by for requests submitted by an external agent, e.g. "agent:agt_hotel_ext"


def agent_of(created_by: str) -> Optional[str]:
    """The agent ID if an external agent submitted the request, else None.

    Agents understand the request themselves and send structured data, so node 1 is skipped
    for them: nothing was interpreted, so there is nothing to reject. They re-query instead."""
    return created_by[len(AGENT_PREFIX):] if created_by.startswith(AGENT_PREFIX) else None


class Pipeline:
    def __init__(self, conn: sqlite3.Connection, deps: Deps,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        conn.row_factory = sqlite3.Row
        self.conn, self.d, self.clock = conn, deps, clock
        self.profiles = ProfileRepo(conn)

    # ───────────── public ─────────────

    def create_request(self, buyer_id: str, created_by: str, body: CreateRequest) -> str:
        rid = _new_id("req")
        self.conn.execute(
            "INSERT INTO requests (id, buyer_id, protocol_id, profile_id, created_by, input_mode, raw_text,"
            " structured_json, status, current_attempt, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (rid, buyer_id, body.protocol_id, body.profile_id, created_by, body.input_mode, body.raw_text,
             body.structured.model_dump_json(), "NO_CANDIDATES", 0, self.clock().isoformat()))
        self.conn.commit()
        self.run(rid)
        return rid

    def run(self, request_id: str, from_node: int = 1, constraints: Optional[list[dict]] = None) -> None:
        req = self._request_row(request_id)
        prev_attempt = req["current_attempt"]
        attempt = prev_attempt + 1
        if prev_attempt == 0:
            from_node = 1
        constraints = self._constraints(request_id) if constraints is None else constraints

        protocol = self.d.protocols.get_active(req["protocol_id"], None)
        profile = self.profiles.get(req["profile_id"]) if req["profile_id"] else None
        rules = self.d.protocols.effective_rules(protocol, profile)
        structured = StructuredRequest.model_validate_json(req["structured_json"])

        out = _Attempt(self, request_id, attempt, from_node, prev_attempt)
        structured = self._node1(out, structured, constraints, profile.body.name if profile else None,
                                 agent_of(req["created_by"]), req["input_mode"] == "NATURAL_LANGUAGE")
        kept, blocked = self._node2(out, structured, rules, constraints)
        if self.d.audit and from_node <= 2:     # a rerun from node 3 or 4 re-uses node 2, nothing new was blocked
            record_blocked(self.d.audit, request_id, attempt, blocked, protocol.body.buyer_id,
                           agent_of(req["created_by"]), _actor(req["created_by"]))
        drafts = self._node3(out, structured, rules, kept, constraints, protocol.body.buyer_id, protocol.body.agent_id)
        self._node4(out, structured, rules, drafts, blocked, constraints)
        out.flush()

        self.conn.execute(
            "UPDATE chain_nodes SET status = 'SUPERSEDED' WHERE request_id = ? AND attempt = ? AND status = 'OK'",
            (request_id, prev_attempt))
        self.conn.execute(
            "UPDATE requests SET structured_json = ?, status = ?, current_attempt = ? WHERE id = ?",
            (structured.model_dump_json(), "CANDIDATES_READY" if drafts else "NO_CANDIDATES", attempt, request_id))
        self.conn.commit()

    def reject(self, request_id: str, node: int, body: RejectNodeRequest) -> None:
        req = self._request_row(request_id)
        if node not in NODE_NAMES:
            raise PipelineError("NOT_FOUND", f"Node {node} does not exist")
        if node == 1 and agent_of(req["created_by"]):
            raise PipelineError("VALIDATION_ERROR", "Node 1 was skipped for this agent request; "
                                "submit a new request with the corrected conditions instead")
        if body.reason not in REJECT_OPTIONS[node]:
            raise PipelineError("VALIDATION_ERROR", f"{body.reason} is not a reject reason for node {node}",
                                allowed=REJECT_OPTIONS[node])
        if self._retries_left(request_id, node) <= 0:
            raise PipelineError("RETRY_LIMIT", f"Node {node} has already been retried {MAX_RETRIES} times")
        params = dict(body.params)
        if node == 1 and "structured" in params:
            StructuredRequest.model_validate(params["structured"])   # fail before touching the database
        if body.reason in ("EXCLUDE_OPTION", "PICK_OTHER") and "candidate_id" not in params:
            top = self.conn.execute("SELECT id FROM candidates WHERE request_id = ? AND attempt = ? AND rank = 1",
                                    (request_id, req["current_attempt"])).fetchone()
            if top:
                params["candidate_id"] = top["id"]   # default: the current #1
        constraint = {"node": node, "reason": body.reason, "params": params, "note": body.note}
        self.conn.execute(
            "UPDATE chain_nodes SET status = 'REJECTED', reject_reason = ?, constraints_json = ?"
            " WHERE request_id = ? AND attempt = ? AND node = ?",
            (body.reason, json.dumps([constraint]), request_id, req["current_attempt"], node))
        self.conn.commit()
        self.run(request_id, from_node=node)

    def result(self, request_id: str, attempt: Optional[int] = None) -> RequestResult:
        req = self._request_row(request_id)
        attempt = attempt or req["current_attempt"]
        nodes = self.conn.execute(
            "SELECT * FROM chain_nodes WHERE request_id = ? AND attempt = ? ORDER BY node",
            (request_id, attempt)).fetchall()
        if not nodes:
            raise PipelineError("NOT_FOUND", f"Attempt {attempt} does not exist")
        rows = self.conn.execute(
            "SELECT body_json, rank FROM candidates WHERE request_id = ? AND attempt = ?", (request_id, attempt)).fetchall()
        cands = [Candidate.model_validate_json(r["body_json"]) for r in rows]
        ranked = sorted((c for c in cands if c.rank is not None), key=lambda c: c.rank)
        return RequestResult(
            request_id=request_id, status=req["status"], attempt=attempt,
            structured=StructuredRequest.model_validate_json(req["structured_json"]),
            chain=[self._chain_node(n, agent_of(req["created_by"])) for n in nodes], candidates=ranked,
            blocked=[c for c in cands if c.rank is None],
            no_candidate_reason=None if ranked else nodes[-1]["summary"],
        )

    def timeline(self, request_id: str, attempt: Optional[int] = None) -> ChainTimeline:
        r = self.result(request_id, attempt)
        return ChainTimeline(request_id=request_id, attempt=r.attempt,
                             events=sorted((e for n in r.chain for e in n.events), key=lambda e: e.at))

    # ───────────── nodes ─────────────

    def _node1(self, out: "_Attempt", structured: StructuredRequest, constraints: list[dict],
               profile_name: Optional[str], agent_id: Optional[str], from_text: bool = False) -> StructuredRequest:
        for c in _for_node(constraints, 1):
            if "structured" in c["params"]:
                structured = StructuredRequest.model_validate(c["params"]["structured"])
        facts = {"category": structured.category_label or structured.category,
                 "requirements": [r.label or r.code for r in structured.requirements],
                 "quantity": f"{structured.quantity.value:g} {structured.quantity.unit}",
                 "delivery_date": structured.delivery_date.isoformat(), "profile": profile_name,
                 "submitted_by_agent": agent_id, "parsed_from_text": from_text}
        if agent_id and from_text:      # an assistant passed on the user's words (MCP): PasStar parsed them itself
            event = self._event(f"Understood the message agent {agent_id} passed on",
                                "Parsed the user's words with the cross-checked parser; the agent answers "
                                "open questions by sending the request again",
                                [EvidenceRef(source="BUYER_INPUT", detail=json.dumps(facts))])
        elif agent_id:
            event = self._event(f"Received a structured request from agent {agent_id}",
                                "The agent understood the request itself; this node was skipped",
                                [EvidenceRef(source="BUYER_INPUT", detail=json.dumps(facts))])
        else:
            event = self._event("Understood the request", "Used the confirmed structured request as is",
                                [EvidenceRef(source="BUYER_INPUT", detail=json.dumps(facts))])
        out.node(1, facts, [event])
        return structured

    def _node2(self, out: "_Attempt", structured: StructuredRequest, rules: EffectiveRules,
               constraints: list[dict]) -> tuple[list[ProductRecord], list]:
        rules = _apply_filter_constraints(rules, constraints)
        res = self.d.catalog.filter(structured, rules)
        blocked = [(p, v) for p, v in res.blocked if p.category == structured.category][:MAX_BLOCKED_SHOWN]
        events = list(res.events) + [self._event(f"Rejected filter: {c['reason']}", "Applied to this request only, profile unchanged",
                                                 [EvidenceRef(source="BUYER_INPUT", detail=json.dumps(c["params"]))])
                                     for c in _for_node(constraints, 2)]
        out.node(2, {"kept": len(res.kept), "blocked": len(res.blocked), "dropped": res.dropped_count}, events)
        return res.kept, blocked

    def _node3(self, out: "_Attempt", structured: StructuredRequest, rules: EffectiveRules,
               kept: list[ProductRecord], constraints: list[dict], buyer_id: str, agent_id: str) -> list[Draft]:
        excluded = self._products_of_candidates(constraints, 3, "EXCLUDE_OPTION")
        weights = dict(rules.weights)
        for c in _for_node(constraints, 3):
            weights.update(c["params"].get("weights", {}))

        convs = {}
        for p in kept:
            if p.id in excluded:
                continue
            conv = self.d.units.convert(p, structured.quantity)
            if conv.sales_units > 0 and conv.qty_covered is not None:   # no defensible conversion: not offered
                convs[p.id] = (p, conv)
            if len(convs) == MAX_CANDIDATES:
                break

        drafts: list[Draft] = []
        prices: dict = {}
        if convs:
            ids = list(convs)
            prices = self.d.valio.prices(ids, buyer_id, delivery_date=structured.delivery_date)
            avail = self.d.valio.availability(ids, structured.delivery_date,
                                              {pid: c.sales_units for pid, (_, c) in convs.items()}, agent_id)
            for pid, (p, conv) in convs.items():
                if pid in prices:
                    drafts.append(_draft(p, conv, prices[pid], avail.get(pid)))
        drafts = rank(drafts, weights, MAX_CANDIDATES)

        top = drafts[0].lines[0].name if drafts else None
        out.node(3, {"count": len(drafts), "top": top, "converted": len(convs), "priced": len(prices),
                     "weights": ", ".join(f"{k} {v:g}" for k, v in sorted(weights.items(), key=lambda kv: -kv[1]))},
                 [self._event("Checked price and stock, then ranked",
                              f"Queried only these {len(convs)} products in one batch, then ranked by profile weights",
                              [EvidenceRef(source="VALIO_PRICE_API", detail=f"Customer prices for {len(convs)} products"),
                               EvidenceRef(source="VALIO_STOCK_API", detail=f"Deliverable on {structured.delivery_date}?")])])
        return drafts

    def _node4(self, out: "_Attempt", structured: StructuredRequest, rules: EffectiveRules, drafts: list[Draft],
               blocked: list, constraints: list[dict]) -> None:
        node4 = _for_node(constraints, 4)
        force_approval = any(c["reason"] == "SEND_TO_APPROVAL" for c in node4)
        picked_away = self._products_of_candidates(constraints, 4, "PICK_OTHER")
        if picked_away:
            drafts = rank([d for d in drafts if not set(d.product_ids) & picked_away], rules.weights, MAX_CANDIDATES)

        candidates: list[Candidate] = []
        for d in drafts:
            unverified = self._data_unverified(d.lines, rules)
            gate = self.d.gate.preview(d.lines, d.totals.gross_cents, rules, unverified)
            outcome, reasons = gate.outcome, [h.description for h in gate.rules_hit]
            if force_approval and OUTCOME_ORDER.index(outcome) < OUTCOME_ORDER.index("BUYER_APPROVAL"):
                outcome, reasons = "BUYER_APPROVAL", reasons + ["You asked to send this to approval"]
            cards = self.d.cards.build(d.lines, structured, rules, gate)
            cand = Candidate(id=_new_id("cand"), rank=d.rank, badge=d.badge, selectable=True, lines=d.lines,
                             totals=d.totals, delivery_date=structured.delivery_date,
                             reliability_level=d.reliability_level, outcome_preview=outcome,
                             outcome_reasons=reasons, cards=cards, explanation="")
            candidates.append(cand)

        # Ranking ran before the gate and the trust check. Now: options the gate would reject (over the order maximum,
        # nothing deliverable) go last; the rest are grouped by their trust check (Reliable, Reliable?, Risky), keeping
        # the score order within each group, so a Risky option never sits above a Reliable one. Recommended is the
        # first allowed, deliverable option.
        candidates = sorted(candidates, key=lambda c: (c.outcome_preview == "REJECT", TRUST_ORDER.get(c.cards.trust.overall, 3)))
        top = next((c.id for c in candidates if c.outcome_preview != "REJECT" and deliverable(c)), None)
        candidates = [c.model_copy(update={"rank": i, "badge": "RECOMMENDED" if c.id == top else None})
                      for i, c in enumerate(candidates, start=1)]

        for p, violations in blocked:
            conv = self.d.units.convert(p, structured.quantity)
            line = CandidateLine(product_id=p.id, name=p.name_en, brand=p.brand, sales_unit=p.sales_unit,
                                 sales_units=conv.sales_units, qty_covered=conv.qty_covered or structured.quantity,
                                 conversion_source=conv.source, available_on_date="UNKNOWN")
            gate = _reject_decision(violations)
            cand = Candidate(id=_new_id("cand"), selectable=False, lines=[line], delivery_date=structured.delivery_date,
                             reliability_level="UNKNOWN", outcome_preview="REJECT",
                             cards=self.d.cards.build([line], structured, rules, gate), violations=violations,
                             explanation="")
            candidates.append(cand)

        top = candidates[0] if candidates and candidates[0].selectable else None
        facts = {"top_outcome": top.outcome_preview if top else None,
                 "top_gross_cents": top.totals.gross_cents if top else None,
                 "top_outcome_text": "; ".join(top.outcome_reasons) if top else None}
        out.node(4, facts, [self._event("Checked against the protocol", "Compared rule by rule with the authorization protocol",
                                        [EvidenceRef(source="RULE", detail=r) for r in (top.outcome_reasons if top else [])])])
        out.candidates(candidates)

    # ───────────── helpers ─────────────

    def _data_unverified(self, lines: list[CandidateLine], rules: EffectiveRules) -> bool:
        check = self.d.unverified or _claim_unverified
        products = [self.d.catalog.get(line.product_id) for line in lines]
        return any(check(p, r) for p in products for r in rules.hard_requirements)

    def _event(self, title: str, logic: str, evidence: list[EvidenceRef]) -> ChainEvent:
        return ChainEvent(at=self.clock(), title=title, logic=logic, evidence=evidence)

    def _request_row(self, request_id: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
        if row is None:
            raise PipelineError("NOT_FOUND", f"Request {request_id} does not exist")
        return row

    def _constraints(self, request_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT constraints_json FROM chain_nodes WHERE request_id = ? AND status = 'REJECTED' ORDER BY attempt, node",
            (request_id,)).fetchall()
        return [c for r in rows for c in json.loads(r["constraints_json"])]

    def _retries_left(self, request_id: str, node: int) -> int:
        (used,) = self.conn.execute(
            "SELECT COUNT(*) FROM chain_nodes WHERE request_id = ? AND node = ? AND status = 'REJECTED'",
            (request_id, node)).fetchone()
        return MAX_RETRIES - used

    def _products_of_candidates(self, constraints: list[dict], node: int, reason: str) -> set[str]:
        """Product IDs of the candidates named by `reason` constraints on `node`."""
        out: set[str] = set()
        for c in _for_node(constraints, node):
            cid = c["params"].get("candidate_id") if c["reason"] == reason else None
            row = cid and self.conn.execute("SELECT body_json FROM candidates WHERE id = ?", (cid,)).fetchone()
            if row:
                out.update(l.product_id for l in Candidate.model_validate_json(row["body_json"]).lines)
        return out

    def _chain_node(self, row: sqlite3.Row, agent_id: Optional[str]) -> ChainNode:
        n, left = row["node"], self._retries_left(row["request_id"], row["node"])
        skipped = n == 1 and agent_id is not None
        return ChainNode(node=n, name=NODE_NAMES[n], attempt=row["attempt"], status=row["status"], summary=row["summary"],
                         can_reject=left > 0 and not skipped, retries_left=0 if skipped else left,
                         reject_options=[] if skipped else REJECT_OPTIONS[n],
                         events=[ChainEvent.model_validate(e) for e in json.loads(row["timeline_json"])])


class _Attempt:
    """Collects one attempt's rows, then writes them in flush().

    Nodes before from_node are copied from the previous attempt. All new texts (node summaries and
    candidate explanations) are written in one ExplainApi.explain_attempt call, i.e. one LLM call."""

    def __init__(self, p: Pipeline, request_id: str, attempt: int, from_node: int, prev_attempt: int) -> None:
        self.p, self.request_id, self.attempt, self.from_node, self.prev = p, request_id, attempt, from_node, prev_attempt
        self._nodes: list[tuple[int, dict, list[ChainEvent]]] = []
        self._cands: list[Candidate] = []

    def node(self, n: int, facts: dict, events: list[ChainEvent]) -> None:
        self._nodes.append((n, facts, events))

    def candidates(self, cands: list[Candidate]) -> None:
        self._cands = list(cands)

    def flush(self) -> None:
        pack = self.p.d.disclosure.pack_for_llm
        fresh = [(n, facts, events) for n, facts, events in self._nodes if n >= self.from_node]
        summaries, explanations = self.p.d.explain.explain_attempt(
            {n: pack(facts, LLM) for n, facts, _ in fresh},
            [pack(c.model_dump(mode="json", exclude={"explanation"}), LLM) for c in self._cands])

        conn = self.p.conn
        for n, _, _ in self._nodes:
            if n < self.from_node:
                conn.execute(
                    "INSERT INTO chain_nodes (id, request_id, node, attempt, status, summary, timeline_json, created_at)"
                    " SELECT ?, request_id, node, ?, 'OK', summary, timeline_json, created_at FROM chain_nodes"
                    " WHERE request_id = ? AND attempt = ? AND node = ?",
                    (_new_id("node"), self.attempt, self.request_id, self.prev, n))
        for n, _, events in fresh:
            conn.execute(
                "INSERT INTO chain_nodes (id, request_id, node, attempt, status, summary, timeline_json, created_at)"
                " VALUES (?, ?, ?, ?, 'OK', ?, ?, ?)",
                (_new_id("node"), self.request_id, n, self.attempt, summaries[n],
                 json.dumps([e.model_dump(mode="json") for e in events]), self.p.clock().isoformat()))
        cands = [c.model_copy(update={"explanation": text}) for c, text in zip(self._cands, explanations)]
        conn.executemany(
            "INSERT INTO candidates (id, request_id, attempt, rank, badge, selectable, body_json) VALUES (?,?,?,?,?,?,?)",
            [(c.id, self.request_id, self.attempt, c.rank, c.badge, int(c.selectable), c.model_dump_json()) for c in cands])


def _actor(created_by: str) -> str:
    return created_by if created_by.startswith(AGENT_PREFIX) else f"user:{created_by}"


def _for_node(constraints: list[dict], node: int) -> list[dict]:
    return [c for c in constraints if c["node"] == node]


def _apply_filter_constraints(rules: EffectiveRules, constraints: list[dict]) -> EffectiveRules:
    """ALLOW_BRAND / EXCLUDE_PRODUCT only for this request; hard requirements are never relaxed."""
    brands, product_ids = list(rules.excluded_brands), list(rules.excluded_product_ids)
    for c in _for_node(constraints, 2):
        if c["reason"] == "ALLOW_BRAND" and c["params"].get("brand") in brands:
            brands.remove(c["params"]["brand"])
        if c["reason"] == "EXCLUDE_PRODUCT" and c["params"].get("product_id"):
            product_ids.append(c["params"]["product_id"])
    return EffectiveRules(**{**rules.__dict__, "excluded_brands": brands, "excluded_product_ids": product_ids})


def _draft(p: ProductRecord, conv, price, avail) -> Draft:
    net = round(price.unit_price_cents * conv.sales_units)
    vat = round(net * price.vat_rate)
    deposit = round(price.deposit_cents_per_unit * conv.sales_units)
    line = CandidateLine(
        product_id=p.id, name=p.name_en, brand=p.brand, sales_unit=p.sales_unit, sales_units=conv.sales_units,
        qty_covered=conv.qty_covered, overbuy=conv.overbuy, conversion_source=conv.source,
        unit_price_cents=price.unit_price_cents, line_net_cents=net, vat_rate=price.vat_rate, deposit_cents=deposit,
        available_on_date=avail.status if avail else "UNKNOWN",
        deliverable_sales_units=avail.deliverable_sales_units if avail and avail.status == "PARTIAL" else None,
    )
    return Draft(lines=[line], totals=Totals(net_cents=net, vat_cents=vat, deposit_cents=deposit, gross_cents=net + vat + deposit),
                 reliability_level=reliability_level(p.delivery_reliability))


def _claim_unverified(product: ProductRecord, r: Requirement) -> bool:
    return r.kind == "CLAIM" and product.claims.get(r.code) != "STRUCTURED"


def _reject_decision(violations: list[Violation]) -> GateDecision:
    return GateDecision(outcome="REJECT", summary="; ".join(v.detail for v in violations),
                        rules_hit=[RuleHit(rule_id=v.rule, description=v.detail, outcome="REJECT") for v in violations])
