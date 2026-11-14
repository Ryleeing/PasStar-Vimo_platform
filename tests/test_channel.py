"""BE1 channel & transactions: protocols, quotes, gate, orders, approvals, audit.
The BE2 / BE3 interfaces are faked; the request and its candidates are rows in the BE3 tables."""
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import db, demo_keys, deps, fixtures as fx
from app.channel import audit, checkpoints, gate, protocols, quotes, seed, signing
from app.channel.authorization import sign
from app.errors import ApiError
from app.internal import Audience, AvailabilityInfo, PriceInfo
from app.main import app
from app.schemas import BuyerAuthorizationClaims, OpenQuestion, Quantity

CHEF, MANAGER, VALIO, OWNER = ({"X-Demo-User": u} for u in ("usr_chef", "usr_manager", "usr_valio", "usr_owner"))
HOTEL_AGENT, EXT_AGENT = {"X-Agent-Key": "demo-hotel"}, {"X-Agent-Key": "demo"}
# 6,000 litres: more than the API can quote, goes to Valio
BULK = fx.CAND_2.model_copy(update={"id": "cand_bulk", "lines": [fx.CAND_2.lines[0].model_copy(update={
    "product_id": "BULK", "sales_units": 6000, "qty_covered": Quantity(value=6000, unit="L")})]})


class FakeValio:
    def __init__(self):
        self.price = {"463038": 1760, "464306": 85, "464201": 95, "BULK": 40}
        self.stock = {}

    def prices(self, ids, buyer_id):
        return {i: PriceInfo(self.price[i], 0.135, 0) for i in ids}

    def availability(self, ids, delivery_date, sales_units, agent_id):
        return {i: self.stock.get(i, AvailabilityInfo("YES", None)) for i in ids}


class FakeDisclosure:
    def filter(self, obj, audience):
        return obj


class FakeParser:
    def draft_protocol(self, buyer_id, agent_id, description):
        return fx.PROTOCOL.body, [OpenQuestion(field="hard_requirements", question="Should nuts be excluded?")]


def add_request():
    """What BE3 writes: request req_1 by the chef, with its candidates."""
    db.run("INSERT INTO requests (id, buyer_id, protocol_id, created_by, input_mode, raw_text, structured_json, status,"
           " created_at) VALUES ('req_1', 'buy_hotel', 'prot_hotel_v1', 'usr_chef', 'NATURAL_LANGUAGE', '1000 l milk', ?,"
           " 'CANDIDATES_READY', ?)", (fx.STRUCTURED.model_dump_json(), db.now().isoformat()))
    for rank, cand in enumerate([fx.CAND_1, fx.CAND_2, fx.CAND_3, BULK], 1):
        db.run("INSERT INTO candidates (id, request_id, attempt, rank, selectable, body_json) VALUES (?, 'req_1', 1, ?, 1, ?)",
               (cand.id, rank, cand.model_dump_json()))


@pytest.fixture
def c(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init()
    seed.reset(onboarding=False)
    add_request()
    deps.valio, deps.disclosure, deps.parser = FakeValio(), FakeDisclosure(), FakeParser()
    return TestClient(app)


def quote(candidate, protocol_id="prot_hotel_v1"):
    return quotes.create("req_1", candidate, protocols.get(protocol_id))


def order(c, quote_id, key, headers=CHEF, protocol_id="prot_hotel_v1", **extra):
    return c.post("/v1/orders", json={"quote_id": quote_id, "protocol_id": protocol_id, **extra},
                  headers={**headers, "Idempotency-Key": key})


def decide(c, approval_id, headers, decision="APPROVE", **body):
    return c.post(f"/v1/approvals/{approval_id}/decision", json={"decision": decision, **body}, headers=headers)


# ───── Quotes, automatic orders, idempotency ─────

def test_auto_order_is_idempotent(c):
    q = quote(fx.CAND_1)
    assert q.totals.gross_cents == 99_880
    assert c.get(f"/v1/quotes/{q.id}/verify").json() == {"quote_id": q.id, "signature_valid": True, "expired": False, "key_id": "valio-1"}
    o1, o2 = order(c, q.id, "k1").json(), order(c, q.id, "k1").json()
    assert o1["status"] == "PLACED" and o1 == o2
    assert [h["rule_id"] for h in o1["gate"]["rules_hit"]] == ["R-SPEND-AUTO"]
    assert quotes.get(q.id).status == "USED"
    assert order(c, q.id, "k2").json()["error"]["code"] == "QUOTE_EXPIRED"       # one quote, one order


def test_idempotency_key_is_scoped_to_the_buyer(c):
    hotel = order(c, quote(fx.CAND_1).id, "same").json()
    db.run("UPDATE requests SET created_by = 'usr_owner', buyer_id = 'buy_bistro'")
    bistro_quote = quote(fx.CAND_1, "prot_bistro_v1").id
    bistro = order(c, bistro_quote, "same", headers=OWNER, protocol_id="prot_bistro_v1").json()
    assert bistro["id"] != hotel["id"] and bistro["quote_id"] == bistro_quote and bistro["status"] == "PLACED"
    assert order(c, bistro_quote, "same", headers=OWNER, protocol_id="prot_bistro_v1").json() == bistro


def test_tampered_quote_fails_verification(c):
    q = quote(fx.CAND_1)
    db.run("UPDATE quotes SET body_json = replace(body_json, '99880', '1') WHERE id = ?", (q.id,))
    assert c.get(f"/v1/quotes/{q.id}/verify").json()["signature_valid"] is False
    assert order(c, q.id, "k1").status_code == 403


def test_expired_quote_is_refused(c, monkeypatch):
    q = quote(fx.CAND_1)
    later = db.now() + timedelta(minutes=16)
    monkeypatch.setattr(db, "now", lambda: later)
    r = order(c, q.id, "k1")
    assert r.status_code == 409 and r.json()["error"]["code"] == "QUOTE_EXPIRED"


def test_blocked_candidate_cannot_be_quoted(c):
    with pytest.raises(ApiError) as e:
        quote(fx.BLOCKED_1)
    assert e.value.code == "OUT_OF_SCOPE"


def test_other_buyer_and_wrong_agent_are_refused(c):
    q = quote(fx.CAND_1)
    assert order(c, q.id, "k1", headers=OWNER).json()["error"]["code"] == "OUT_OF_SCOPE"
    assert order(c, q.id, "k2", headers=EXT_AGENT).json()["error"]["code"] == "PROTOCOL_INVALID"
    assert order(c, q.id, "k3", headers={"X-Agent-Key": "nope"}).json()["error"]["code"] == "PROTOCOL_INVALID"


# ───── Gate ─────

def test_gate_takes_the_strictest_rule(c):
    rules = protocols.effective_rules(protocols.get("prot_hotel_v1"), None)
    line = fx.CAND_3.lines[0]
    partial = gate.preview([line.model_copy(update={"available_on_date": "PARTIAL"})], 107_825, rules, True)
    assert partial.outcome == "BUYER_APPROVAL"
    assert [h.rule_id for h in partial.rules_hit] == ["R-DELIVERY-PARTIAL", "R-SPEND-APPROVAL", "R-DATA-UNVERIFIED"]
    assert gate.preview([line.model_copy(update={"available_on_date": "NO"})], 90_000, rules, False).outcome == "REJECT"
    assert gate.preview([line], 300_001, rules, False).outcome == "REJECT"
    cross = gate.preview([line.model_copy(update={"substitution_tier": "OFFICIAL"})], 90_000, rules, False)
    assert cross.outcome == "AUTO" and [h.rule_id for h in cross.rules_hit] == ["R-SUB-OFFICIAL", "R-SPEND-AUTO"]


def test_over_the_maximum_is_rejected(c):
    deps.valio.price["BULK"] = 60
    assert order(c, quote(BULK).id, "k1").json()["status"] == "REJECTED"


# ───── Buyer approval ─────

def test_buyer_approval_then_order(c):
    o = order(c, quote(fx.CAND_3).id, "k1").json()
    assert o["status"] == "PENDING_BUYER_APPROVAL"
    card = c.get("/v1/approvals", params={"kind": "BUYER", "status": "PENDING"}, headers=MANAGER).json()[0]
    assert card["id"] == o["approval_id"] and card["reason_codes"] == ["R-SPEND-APPROVAL"]
    assert card["requested_by"] == "usr_chef" and card["approver"] == "usr_manager" and card["candidate"]["id"] == "cand_3"
    assert c.get("/v1/approvals", headers=OWNER).json() == []                     # other buyers cannot see it

    assert decide(c, card["id"], CHEF).json()["error"]["code"] == "SELF_APPROVAL_FORBIDDEN"
    assert decide(c, card["id"], HOTEL_AGENT).json()["error"]["code"] == "NOT_APPROVER"
    assert decide(c, card["id"], OWNER).status_code == 404
    r = decide(c, card["id"], MANAGER).json()
    assert r["approval"]["status"] == "APPROVED" and r["order"]["status"] == "PLACED"
    assert c.get(f"/v1/orders/{o['id']}", headers=CHEF).json()["status"] == "PLACED"
    assert decide(c, card["id"], MANAGER).status_code == 422                      # already decided


def test_price_change_after_approval_needs_a_new_quote(c):
    q = quote(fx.CAND_3)
    o = order(c, q.id, "k1").json()
    deps.valio.price["464201"] = 99
    r = decide(c, o["approval_id"], MANAGER).json()["order"]
    assert r["status"] == "REQUOTE_REQUIRED" and quotes.get(q.id).status == "SUPERSEDED"
    new = quotes.get(r["new_quote_id"])
    assert new.lines[0].unit_price_cents == 99 and new.status == "VALID"
    assert order(c, new.id, "k2").json()["status"] == "PENDING_BUYER_APPROVAL"   # the new quote goes through the gate and approval again


def test_rejected_approval_rejects_the_order(c):
    o = order(c, quote(fx.CAND_3).id, "k1").json()
    r = decide(c, o["approval_id"], MANAGER, "REJECT", note="Too expensive").json()
    assert r["approval"]["status"] == "REJECTED" and r["order"]["status"] == "REJECTED"


def test_failed_decision_leaves_nothing_behind(c):
    o = order(c, quote(fx.CAND_3).id, "k1").json()
    c.post("/v1/protocols/prot_hotel_v1/revoke", headers=MANAGER)
    audit_before = len(audit.entries())
    assert decide(c, o["approval_id"], MANAGER).json()["error"]["code"] == "PROTOCOL_INVALID"
    assert c.get(f"/v1/approvals/{o['approval_id']}", headers=MANAGER).json()["status"] == "PENDING"
    assert c.get(f"/v1/orders/{o['id']}", headers=CHEF).json()["status"] == "PENDING_BUYER_APPROVAL"
    assert len(audit.entries()) == audit_before
    r = decide(c, o["approval_id"], MANAGER, "REJECT", note="Protocol revoked").json()      # still decidable
    assert r["approval"]["status"] == "REJECTED" and r["order"]["status"] == "REJECTED"
    assert c.get("/v1/audit/verify").json()["ok"] is True


def test_self_mode_owner_approves_own_order(c):
    db.run("UPDATE requests SET created_by = 'usr_owner', buyer_id = 'buy_bistro'")
    o = order(c, quote(fx.CAND_3, "prot_bistro_v1").id, "k1", headers=OWNER, protocol_id="prot_bistro_v1").json()
    assert o["status"] == "PENDING_BUYER_APPROVAL"                                # in SELF mode approval is still a separate step
    assert decide(c, o["approval_id"], OWNER).json()["order"]["status"] == "PLACED"
    assert "ORDER_SELF_APPROVED" in [e["event"] for e in audit.entries()]


# ───── Approve with changes: proposal parameters only, re-validated by the gateway ─────

def test_buyer_approver_reduces_quantity(c):
    o = order(c, quote(fx.CAND_3).id, "k1").json()
    change = {"lines": [{"product_id": "464201", "sales_units": 900}]}
    assert decide(c, o["approval_id"], MANAGER, "APPROVE_WITH_CHANGES", changes=change).status_code == 422   # a reason is required
    for bad in ({"lines": [{"product_id": "464201", "sales_units": 1200}]},                   # quantity can only go down
                {"lines": [{"product_id": "464201", "unit_price_cents": 50}]},                # buyers cannot change prices
                {"hard_requirements": []}):                                                   # read-only
        assert decide(c, o["approval_id"], MANAGER, "APPROVE_WITH_CHANGES", changes=bad, note="x").status_code == 422
    r = decide(c, o["approval_id"], MANAGER, "APPROVE_WITH_CHANGES", changes=change, note="900 L is enough").json()
    assert r["approval"]["status"] == "APPROVED_WITH_CHANGES" and r["order"]["status"] == "PLACED"
    new = quotes.get(r["order"]["quote_id"])
    assert new.lines[0].sales_units == 900 and new.totals.net_cents == 85_500
    assert signing.verify(new.jws)["counter_offer_of"] == o["quote_id"]
    diff = [e for e in audit.entries() if e["event"] == "PROPOSAL_MODIFIED"][0]["payload"]
    assert diff["note"] == "900 L is enough" and diff["diff"][0]["field"] == "lines.464201.sales_units"


def test_valio_counter_offer_is_a_new_quote_round(c):
    o = order(c, quote(BULK).id, "k1").json()
    assert o["status"] == "PENDING_VALIO_REVIEW"
    assert c.get(f"/v1/approvals/{o['approval_id']}", headers=MANAGER).status_code == 404   # buyers cannot see the Valio queue
    card = c.get("/v1/approvals", params={"kind": "VALIO"}, headers=VALIO).json()[0]
    assert card["reason_codes"] == ["R-OUT-OF-API"] and card["agent_activity"]["agent_id"] == "agt_hotel"

    change = {"lines": [{"product_id": "BULK", "unit_price_cents": 42}], "conditions": ["Prepayment required"]}
    r = decide(c, o["approval_id"], VALIO, "APPROVE_WITH_CHANGES", changes=change, note="Bulk logistics surcharge").json()
    assert r["approval"]["status"] == "APPROVED_WITH_CHANGES" and r["order"]["status"] == "REQUOTE_REQUIRED"
    offer = quotes.get(r["order"]["new_quote_id"])
    assert offer.lines[0].unit_price_cents == 42 and "Prepayment required" in r["order"]["requote_reason"]
    assert signing.verify(offer.jws)["conditions"] == ["Prepayment required"]
    assert c.get(f"/v1/quotes/{offer.id}/verify").json()["signature_valid"]

    # the buyer orders again with the new quote: no second Valio review, spend still needs buyer approval,
    # and the recheck honours the price Valio set
    o2 = order(c, offer.id, "k2").json()
    assert o2["status"] == "PENDING_BUYER_APPROVAL"
    assert decide(c, o2["approval_id"], MANAGER).json()["order"]["status"] == "PLACED"


def test_price_change_beyond_the_reviewer_band_escalates(c):
    o = order(c, quote(BULK).id, "k1").json()
    r = decide(c, o["approval_id"], VALIO, "APPROVE_WITH_CHANGES", note="Big discount",
               changes={"lines": [{"product_id": "BULK", "unit_price_cents": 30}]}).json()
    assert r["approval"]["status"] == "ROUTED"
    assert c.get(f"/v1/orders/{o['id']}", headers=CHEF).json()["status"] == "PENDING_VALIO_REVIEW"


def test_valio_approval_then_buyer_approval(c):
    o = order(c, quote(BULK).id, "k1").json()
    r = decide(c, o["approval_id"], VALIO).json()["order"]
    assert r["status"] == "PENDING_BUYER_APPROVAL" and r["approval_id"] != o["approval_id"]
    assert decide(c, r["approval_id"], MANAGER).json()["order"]["status"] == "PLACED"


# ───── External approval ─────

def token(q, **over):
    now = int(time.time())
    claims = dict(protocol_id="prot_hotel_ext_v1", buyer_id="buy_hotel", quote_id=q.id,
                  amount_gross_cents=q.totals.gross_cents, approver="usr_manager", iat=now, exp=now + 600)
    return sign(BuyerAuthorizationClaims(**{**claims, **over}), demo_keys.load_or_create_private())


def test_external_authorization_flow(c):
    q = quote(fx.CAND_3, "prot_hotel_ext_v1")
    o = order(c, q.id, "k1", headers=EXT_AGENT, protocol_id="prot_hotel_ext_v1").json()
    assert o["status"] == "BUYER_AUTHORIZATION_REQUIRED" and o["approval_id"] is None
    assert o["authorization_request"]["quote_id"] == q.id and o["authorization_request"]["reason_codes"] == ["R-SPEND-APPROVAL"]
    url = f"/v1/orders/{o['id']}/authorization"
    bad = c.post(url, json={"buyer_authorization": token(q, amount_gross_cents=1)}, headers=EXT_AGENT)
    assert bad.status_code == 403 and bad.json()["error"]["details"]["reason"] == "AMOUNT_MISMATCH"
    ok = c.post(url, json={"buyer_authorization": token(q)}, headers=EXT_AGENT).json()
    assert ok["status"] == "PLACED" and ok["authorized_by"] == "usr_manager" and ok["authorization_request"] is None


def test_authorization_can_come_with_the_order(c):
    q = quote(fx.CAND_3, "prot_hotel_ext_v1")
    o = order(c, q.id, "k1", headers=EXT_AGENT, protocol_id="prot_hotel_ext_v1", buyer_authorization=token(q)).json()
    assert o["status"] == "PLACED" and o["authorized_by"] == "usr_manager"


# ───── Protocols ─────

def test_protocol_lifecycle(c):
    p = c.post("/v1/protocols/draft", json={"buyer_id": "buy_hotel", "agent_id": "agt_hotel", "description": "weekly dairy"},
               headers=HOTEL_AGENT).json()
    assert p["status"] == "DRAFT" and p["open_questions"]
    url = f"/v1/protocols/{p['id']}"
    body = {**p["body"], "auto_approve_limit_cents": 50_000}
    assert c.put(url, json={"body": body}, headers=MANAGER).json()["body"]["auto_approve_limit_cents"] == 50_000
    assert c.post(f"{url}/sign", headers=HOTEL_AGENT).json()["error"]["code"] == "NOT_APPROVER"   # an agent cannot activate its own protocol
    assert c.post(f"{url}/confirm", headers=VALIO).json()["error"]["code"] == "PROTOCOL_INVALID"
    assert c.post(f"{url}/sign", headers=MANAGER).json()["status"] == "PENDING_VALIO_CONFIRMATION"
    assert c.post(f"{url}/confirm", headers=MANAGER).json()["error"]["code"] == "NOT_APPROVER"
    active = c.post(f"{url}/confirm", headers=VALIO).json()
    assert active["status"] == "ACTIVE" and active["jws"]
    assert c.put(url, json={"body": body}, headers=MANAGER).json()["error"]["code"] == "PROTOCOL_INVALID"
    assert c.post(f"{url}/revoke", headers=MANAGER).json()["status"] == "REVOKED"
    with pytest.raises(ApiError):
        protocols.get_active(p["id"], "agt_hotel")


def test_amend_tightens_now_and_expands_after_signature(c):
    changes = {"auto_approve_limit_cents": 80_000, "allowed_categories": ["10400", "10401", "10405", "22540"],
               "valio_scope": {"sensitivity_ceiling": "L2"}}
    diff = c.post("/v1/protocols/prot_hotel_v1/amend", json={"changes": changes}, headers=HOTEL_AGENT).json()
    assert [x["field"] for x in diff["tightened"]] == ["auto_approve_limit_cents"]
    assert [x["field"] for x in diff["expanded"]] == ["allowed_categories"]
    assert [x["field"] for x in diff["forbidden"]] == ["valio_scope.sensitivity_ceiling"]
    assert diff["result_status"] == "PENDING_BUYER_SIGNATURE"

    v1 = protocols.get_active("prot_hotel_v1", "agt_hotel").body                  # tightening is live, expansion is not
    assert v1.auto_approve_limit_cents == 80_000 and "22540" not in v1.allowed_categories
    card = c.get(f"/v1/approvals/{diff['approval_id']}", headers=MANAGER).json()
    assert card["subject"] == "PROTOCOL_CHANGE" and card["protocol_diff"]["expanded"][0]["after"] == ["22540"]
    assert decide(c, diff["approval_id"], HOTEL_AGENT).json()["error"]["code"] == "NOT_APPROVER"   # an agent cannot widen its own permissions
    assert decide(c, diff["approval_id"], MANAGER).json()["approval"]["status"] == "APPROVED"

    v2 = c.post("/v1/protocols/prot_hotel_v2/confirm", headers=VALIO).json()
    assert v2["status"] == "ACTIVE" and v2["version"] == 2 and "22540" in v2["body"]["allowed_categories"]
    assert v2["body"]["valio_scope"]["sensitivity_ceiling"] == "L1"                # the forbidden part was dropped
    assert protocols.get("prot_hotel_v1").status == "SUPERSEDED"


def test_profile_can_only_narrow_the_protocol(c):
    rules = protocols.effective_rules(protocols.get("prot_hotel_v1"), fx.PROFILE)
    assert rules.excluded_brands == ["Arla"] and rules.protocol.substitution.cross_category == "FORBIDDEN"
    assert rules.auto_limit_cents == 100_000


# ───── Checkpoints, audit ─────

def test_query_checkpoint(c):
    p = protocols.get("prot_hotel_v1")
    agent = Audience(kind="AGENT", buyer_id="buy_hotel", agent_id="agt_hotel")
    for code, who, ids in (("OUT_OF_SCOPE", Audience(kind="AGENT", buyer_id="buy_hospital", agent_id="agt_hospital"), []),
                           ("PROTOCOL_INVALID", Audience(kind="AGENT", buyer_id="buy_hotel", agent_id="agt_rogue"), []),
                           ("OUT_OF_SCOPE", agent, [str(i) for i in range(9)])):
        with pytest.raises(ApiError) as e:
            checkpoints.check_query(who, p, ids)
        assert e.value.code == code
    for _ in range(30):
        checkpoints.check_query(agent, p, ["463038"])
    with pytest.raises(ApiError) as e:
        checkpoints.check_query(agent, p, ["463038"])
    assert e.value.code == "RATE_LIMITED"


def test_audit_chain_and_role_filter(c):
    q = quote(fx.CAND_1)
    order(c, q.id, "k1")
    assert order(c, q.id, "k2", headers={"X-Agent-Key": "nope"}).status_code == 403     # an unregistered agent
    assert c.get("/v1/audit/verify").json()["ok"] is True

    events = lambda h: [e["event"] for e in c.get("/v1/audit", headers=h).json()]
    assert "AGENT_BLOCKED" in events(VALIO) and "AGENT_BLOCKED" not in events(CHEF)
    assert "ORDER_PLACED" in events(CHEF) and events(OWNER) == []
    assert {e["event"] for e in c.get("/v1/audit", params={"request_id": "req_1"}, headers=CHEF).json()} == \
        {"QUOTE_CREATED", "GATE_DECIDED", "ORDER_PLACED"}

    db.run("UPDATE audit_log SET payload_json = '{}' WHERE seq = 3")
    assert c.get("/v1/audit/verify").json() == {"ok": False, "entries_checked": 2, "first_bad_seq": 3}


def test_errors_use_the_unified_format(c):
    r = c.post("/v1/orders", json={}, headers={**CHEF, "Idempotency-Key": "k"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert c.get("/v1/orders/ord_nope", headers=CHEF).json()["error"]["code"] == "NOT_FOUND"
    assert c.get("/v1/orders/ord_nope").json()["error"]["code"] == "OUT_OF_SCOPE"


# ───── Hold report ─────

def holds(c, headers, **params):
    return c.get("/v1/reports/holds", params=params, headers=headers)


def test_append_copies_hold_fields_into_the_hashed_payload(c):
    seq = audit.append("ORDER_HELD", "system", {"order_id": "ord_x"}, buyer_id="buy_hotel", reason_code="R-SPEND-APPROVAL")
    assert audit.entries()[-1]["payload"] == {"order_id": "ord_x", "buyer_id": "buy_hotel", "reason_code": "R-SPEND-APPROVAL"}
    assert c.get("/v1/audit/verify").json()["ok"] is True
    db.run("UPDATE audit_log SET reason_code = 'R-SPEND-AUTO' WHERE seq = ?", (seq,))      # the column, not the payload
    assert c.get("/v1/audit/verify").json() == {"ok": False, "entries_checked": seq - 1, "first_bad_seq": seq}


def test_held_orders_are_reported_to_their_buyer_only(c):
    o = order(c, quote(fx.CAND_3).id, "k1", headers=HOTEL_AGENT).json()                 # over the auto-approve limit
    order(c, quote(BULK).id, "k2")                                                      # needs Valio sales
    order(c, quote(fx.CAND_1).id, "k3")                                                 # placed: not a hold
    audit.append("ORDER_HELD", "system", {}, buyer_id="buy_hospital", agent_id="agt_hospital", reason_code="R-SPEND-APPROVAL")
    audit.append("AGENT_BLOCKED", "agent:unregistered", {"suppressed": 0}, visibility="VALIO", reason_code="PROTOCOL_INVALID")

    r = holds(c, MANAGER, buyer_id="buy_hospital").json()                               # buyer_id is ignored for buyers
    assert (r["audience"], r["buyer_id"], r["total"]) == ("BUYER", "buy_hotel", 2) and r["by_event"] == {"ORDER_HELD": 2}
    assert {x["reason_code"]: x["count"] for x in r["by_reason"]} == {"R-SPEND-APPROVAL": 1, "R-OUT-OF-API": 1}
    assert r["by_agent"][0]["agent_id"] == "agt_hotel" and r["by_agent"][0]["total"] == 2
    newest, oldest = r["items"]
    assert oldest["order_id"] == o["id"] and oldest["request_id"] == "req_1" and oldest["summary"].startswith("Held for approval")
    assert newest["reason_code"] == "R-OUT-OF-API" and r["missing_attributes"] == []
    held = [e["payload"] for e in audit.entries() if e["event"] == "ORDER_HELD"][0]
    assert held["status"] == "PENDING_BUYER_APPROVAL" and held["gross_cents"] == 107_825 and held["all_reason_codes"] == ["R-SPEND-APPROVAL"]

    v = holds(c, VALIO)
    assert v.json()["total"] == 4 and v.json()["items"] == [] and v.json()["buyer_id"] is None
    assert not any(word in v.text for word in ("summary", "gross_cents", "order_id"))
    assert holds(c, VALIO, buyer_id="buy_hospital").json()["total"] == 1
    assert holds(c, VALIO, agent_id="agt_hotel").json()["by_agent"][0]["total"] == 2


def test_requote_is_a_hold(c):
    q = quote(fx.CAND_1)
    deps.valio.price["463038"] = 1800
    assert order(c, q.id, "k1").json()["status"] == "REQUOTE_REQUIRED"
    assert holds(c, CHEF).json()["by_reason"] == [{"reason_code": "QUOTE_CHANGED", "count": 1}]


def test_missing_attributes_need_two_buyers(c):
    for buyer, attribute in (("buy_hotel", "keto"), ("buy_hospital", "keto"), ("buy_hotel", "keto"), ("buy_hotel", "paleo"),
                             (None, "paleo"), (None, "paleo")):                         # buyer not known yet: no second buyer
        audit.append("ATTRIBUTE_NOT_IN_CATALOG", "user:REQUESTER", {"attribute": attribute}, buyer_id=buyer,
                     reason_code="ATTRIBUTE_NOT_IN_CATALOG", summary=f"'{attribute}' is not an attribute the catalogue can check")
    v = holds(c, VALIO).json()
    assert v["missing_attributes"] == [{"attribute": "keto", "count": 3}]
    assert v["total"] == 0 and v["by_event"] == {} and v["by_reason"] == []            # only in missing_attributes
    b = holds(c, MANAGER).json()
    assert b["missing_attributes"] == [] and b["by_event"] == {"ATTRIBUTE_NOT_IN_CATALOG": 3}
    assert b["items"][0]["reason_code"] == "ATTRIBUTE_NOT_IN_CATALOG" and b["items"][0]["request_id"] is None


def test_report_is_for_people_and_at_most_90_days(c):
    r = holds(c, HOTEL_AGENT)
    assert r.status_code == 403 and r.json()["error"]["code"] == "OUT_OF_SCOPE"
    assert holds(c, {}).status_code == 403
    r = holds(c, VALIO, since="2026-01-01T00:00:00", until="2026-04-02T00:00:00")       # 91 days
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert holds(c, VALIO, since="2026-01-01T00:00:00", until="2026-04-01T00:00:00").json()["total"] == 0


def test_blocked_agent_is_logged_once_a_minute(c, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(checkpoints.time, "monotonic", lambda: clock[0])
    for _ in range(10):
        checkpoints.record_block("demo-rogue", "POST /v1/requests", "RATE_LIMITED", "More than 30 queries per minute")
    blocked = lambda: [e["payload"] for e in c.get("/v1/audit", headers=VALIO).json() if e["event"] == "AGENT_BLOCKED"]
    assert blocked() == [{"buyer_id": "buy_hotel", "agent_id": "agt_rogue", "reason_code": "RATE_LIMITED",
                          "endpoint": "POST /v1/requests", "suppressed": 9}]
    checkpoints.record_block("demo-rogue", "POST /v1/requests", "RATE_LIMITED", "More than 30 queries per minute")
    assert len(blocked()) == 1                                                          # same minute: held back
    clock[0] += 61
    assert [b["suppressed"] for b in blocked()] == [9, 0]
    report = holds(c, VALIO).json()
    assert report["by_agent"] == [{"agent_id": "agt_rogue", "total": 11, "by_reason": [{"reason_code": "RATE_LIMITED", "count": 11}]}]
    assert holds(c, MANAGER).json()["total"] == 11                                      # the hotel sees its own agent's blocks
    assert c.get("/v1/audit/verify").json()["ok"] is True


def test_old_audit_log_gets_the_hold_columns(c):
    db.run("DROP INDEX idx_audit_holds")
    for column in audit.COLUMNS:
        db.run(f"ALTER TABLE audit_log DROP COLUMN {column}")
    audit_before = db.one("SELECT COUNT(*) AS n FROM audit_log")["n"]
    db.init()
    assert c.get("/v1/audit/verify").json() == {"ok": True, "entries_checked": audit_before, "first_bad_seq": None}
