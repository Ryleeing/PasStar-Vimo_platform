"""BE3 hold events: what parse and the pipeline write to the audit log (in-memory stand-in for now)."""
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import fixtures as fx
from app.main import app
from app.channel import audit
from app.orchestrator.explain import TemplateExplain
from app.orchestrator.holds import HoldCaller, normalize_attribute, record_parse
from app.orchestrator.parser import ParseOutcome
from app.orchestrator.pipeline import Deps, Pipeline
from app.orchestrator.profiles import ProfileRepo
from app.orchestrator.stubs import (
    StubAudit, StubCards, StubCatalog, StubDisclosure, StubGate, StubProtocols, StubUnits, StubValio,
)
from app.schemas import CreateRequest, RejectNodeRequest

SCHEMA = Path(__file__).resolve().parents[1] / "backend" / "app" / "schema.sql"
CALLER = HoldCaller(buyer_id="buy_hotel", agent_id="agt_hotel", actor="agent:agt_hotel")


def outcome(ambiguity_fields=(), unknown=()):
    return ParseOutcome(fx.STRUCTURED, [{"field": f, "question": "?", "options": []} for f in ambiguity_fields],
                        [], list(unknown), "rules")


# ───── parse ─────

def test_blocking_question_writes_one_needs_input_event():
    audit = StubAudit()
    assert record_parse(audit, outcome(["delivery_date", "quantity", "substitution"]), CALLER) == 1
    (e,) = audit.of("REQUEST_NEEDS_INPUT")
    assert e["reason_code"] == "NEEDS_QUANTITY"                       # highest-priority field wins
    assert e["payload"]["fields"] == ["quantity", "delivery_date"]
    assert e["visibility"] == "BUYER" and e["request_id"] is None
    assert e["buyer_id"] == e["payload"]["buyer_id"] == "buy_hotel"   # column and payload copy agree


def test_non_blocking_questions_write_nothing():
    audit = StubAudit()
    assert record_parse(audit, outcome(["substitution", "other"]), CALLER) == 0


def test_unknown_attributes_one_event_each_normalized_and_deduplicated():
    audit = StubAudit()
    record_parse(audit, outcome(["requirements"], unknown=["  Keto ", "keto", "Paleo friendly"]), CALLER)
    events = audit.of("ATTRIBUTE_NOT_IN_CATALOG")
    assert [e["payload"]["attribute"] for e in events] == ["keto", "paleo friendly"]
    assert all(e["visibility"] == "BOTH" and e["reason_code"] == "ATTRIBUTE_NOT_IN_CATALOG" for e in events)
    assert audit.of("REQUEST_NEEDS_INPUT")[0]["reason_code"] == "NEEDS_REQUIREMENTS"


def test_attribute_is_capped_at_40_characters():
    assert len(normalize_attribute("x" * 100)) == 40


def test_parse_route_writes_hold_events_to_the_real_audit_log():
    before = len(audit.entries())
    r = TestClient(app).post("/v1/requests/parse", json={"text": "lactose-free milk tomorrow", "profile_id": "prof_banquet"},
                             headers={"X-Demo-User": "usr_chef"})
    assert r.status_code == 200
    new = audit.entries()[before:]
    assert [e["event"] for e in new] == ["REQUEST_NEEDS_INPUT"]
    e = new[0]
    assert e["payload"]["reason_code"] == "NEEDS_QUANTITY" and e["payload"]["buyer_id"] == "buy_hotel"
    assert e["actor"] == "user:usr_chef" and e["visibility"] == "BUYER"
    assert audit.verify().ok                                   # columns and hashed payload still agree


def test_parse_route_needs_a_known_caller():
    r = TestClient(app).post("/v1/requests/parse", json={"text": "milk"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "OUT_OF_SCOPE"


# ───── pipeline ─────

@pytest.fixture
def pipeline():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA.read_text())
    conn.execute("INSERT INTO buyers (id, name) VALUES ('buy_hotel', 'Hotel Helsinki Central')")
    ProfileRepo(conn).create("buy_hotel", fx.PROFILE.body, profile_id="prof_banquet")
    audit = StubAudit()
    deps = Deps(catalog=StubCatalog(), units=StubUnits(), valio=StubValio(), cards=StubCards(),
                disclosure=StubDisclosure(), protocols=StubProtocols(), gate=StubGate(),
                explain=TemplateExplain(), audit=audit)
    return Pipeline(conn, deps, clock=lambda: fx.NOW), audit


def test_blocked_candidates_are_recorded_per_rule(pipeline):
    p, audit = pipeline
    rid = p.create_request("buy_hotel", "agent:agt_hotel", CreateRequest(
        protocol_id="prot_hotel_v1", profile_id="prof_banquet", input_mode="PANEL", structured=fx.STRUCTURED))
    (e,) = audit.of("CANDIDATES_BLOCKED")
    assert e["reason_code"] == "PROFILE_EXCLUDED_BRAND" and e["payload"]["product_ids"] == ["464179"]
    assert e["request_id"] == rid and e["payload"]["attempt"] == 1
    assert (e["buyer_id"], e["agent_id"], e["actor"]) == ("buy_hotel", "agt_hotel", "agent:agt_hotel")


def test_rerun_from_node_3_does_not_record_the_same_block_again(pipeline):
    p, audit = pipeline
    rid = p.create_request("buy_hotel", "usr_chef", CreateRequest(
        protocol_id="prot_hotel_v1", profile_id="prof_banquet", input_mode="PANEL", structured=fx.STRUCTURED))
    p.reject(rid, 3, RejectNodeRequest(reason="PREFER_SINGLE_SKU"))
    assert len(audit.of("CANDIDATES_BLOCKED")) == 1
    p.reject(rid, 2, RejectNodeRequest(reason="TOO_STRICT"))
    assert len(audit.of("CANDIDATES_BLOCKED")) == 2          # node 2 ran again: a new attempt, a new event


def test_allow_brand_rerun_records_no_block(pipeline):
    p, audit = pipeline
    rid = p.create_request("buy_hotel", "usr_chef", CreateRequest(
        protocol_id="prot_hotel_v1", profile_id="prof_banquet", input_mode="PANEL", structured=fx.STRUCTURED))
    p.reject(rid, 2, RejectNodeRequest(reason="ALLOW_BRAND", params={"brand": "Arla"}))
    assert [e["payload"]["attempt"] for e in audit.of("CANDIDATES_BLOCKED")] == [1]
