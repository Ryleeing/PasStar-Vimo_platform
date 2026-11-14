"""Pipeline state machine: four nodes, attempts, node rejection, retry limit. Uses local BE1/BE2 stubs."""
import sqlite3
from pathlib import Path

import pytest

from app import fixtures as fx
from app.orchestrator.explain import TemplateExplain
from app.orchestrator.pipeline import MAX_CANDIDATES, Deps, Pipeline, PipelineError
from app.orchestrator.profiles import ProfileRepo
from app.orchestrator.stubs import (
    StubCards, StubCatalog, StubDisclosure, StubGate, StubProtocols, StubUnits, StubValio,
)
from app.schemas import CreateRequest, Quantity, RejectNodeRequest

SCHEMA = Path(__file__).resolve().parents[1] / "backend" / "app" / "schema.sql"


@pytest.fixture
def env():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA.read_text())
    conn.execute("INSERT INTO buyers (id, name) VALUES ('buy_hotel', 'Hotel Helsinki Central')")
    ProfileRepo(conn).create("buy_hotel", fx.PROFILE.body, profile_id="prof_banquet")
    valio = StubValio()
    deps = Deps(catalog=StubCatalog(), units=StubUnits(), valio=valio, cards=StubCards(), disclosure=StubDisclosure(),
                protocols=StubProtocols(), gate=StubGate(), explain=TemplateExplain())
    return Pipeline(conn, deps, clock=lambda: fx.NOW), valio


def new_request(p: Pipeline, profile_id="prof_banquet") -> str:
    body = CreateRequest(protocol_id="prot_hotel_v1", profile_id=profile_id, input_mode="PANEL", structured=fx.STRUCTURED)
    return p.create_request("buy_hotel", "usr_chef", body)


def reject(p, rid, node, reason, **params):
    p.reject(rid, node, RejectNodeRequest(reason=reason, params=params))
    return p.result(rid)


# ───── first run ─────

def test_first_run_has_four_ok_nodes_and_ranked_candidates(env):
    p, _ = env
    r = p.result(new_request(p))
    assert r.status == "CANDIDATES_READY" and r.attempt == 1
    assert [(n.node, n.name, n.status, n.attempt) for n in r.chain] == [
        (1, "UNDERSTAND", "OK", 1), (2, "FILTER", "OK", 1), (3, "RANK", "OK", 1), (4, "GATE", "OK", 1)]
    assert [c.rank for c in r.candidates] == list(range(1, len(r.candidates) + 1))
    assert len(r.candidates) <= MAX_CANDIDATES
    assert [c.badge for c in r.candidates[:3]] == ["RECOMMENDED", "CHEAPEST", "MOST_RELIABLE"]
    assert all(n.summary for n in r.chain) and all(c.explanation for c in r.candidates)


def test_profile_excluded_brand_is_blocked_not_candidate(env):
    p, _ = env
    r = p.result(new_request(p))
    assert "464179" not in {l.product_id for c in r.candidates for l in c.lines}
    (arla,) = r.blocked
    assert arla.lines[0].product_id == "464179" and not arla.selectable and arla.rank is None
    assert arla.violations and arla.violations[0].rule == "PROFILE_EXCLUDED_BRAND"


def test_irrelevant_products_neither_candidate_nor_blocked(env):
    p, _ = env
    r = p.result(new_request(p))
    ids = {l.product_id for c in r.candidates + r.blocked for l in c.lines}
    assert "999002" not in ids      # milk without the lactose-free claim
    assert "469869" not in ids      # cheese, other category


def test_prices_and_stock_are_queried_once_in_one_batch(env):
    p, valio = env
    new_request(p)
    assert [kind for kind, _ in valio.calls] == ["prices", "availability"]
    assert len(valio.calls[0][1]) <= MAX_CANDIDATES


def test_name_only_claim_needs_approval(env):
    p, _ = env
    r = p.result(new_request(p))
    (farm,) = [c for c in r.candidates if c.lines[0].product_id == "999001"]
    assert farm.outcome_preview == "BUYER_APPROVAL"
    assert farm.cards.allergen.rows[0].trust_grade == "C"


def test_totals_add_up(env):
    p, _ = env
    for c in p.result(new_request(p)).candidates:
        t = c.totals
        assert t.gross_cents == t.net_cents + t.vat_cents + t.deposit_cents
        assert c.lines[0].qty_covered.value >= 1000


# ───── rejecting nodes ─────

def test_allow_brand_reruns_from_node_2_and_keeps_node_1(env):
    p, _ = env
    rid = new_request(p)
    first = p.result(rid)
    r = reject(p, rid, 2, "ALLOW_BRAND", brand="Arla")
    assert r.attempt == 2 and r.blocked == []
    assert "464179" in {c.lines[0].product_id for c in r.candidates}
    assert r.chain[0].summary == first.chain[0].summary
    old = p.result(rid, attempt=1)
    assert [n.status for n in old.chain] == ["SUPERSEDED", "REJECTED", "SUPERSEDED", "SUPERSEDED"]


def test_allow_brand_does_not_change_profile(env):
    p, _ = env
    rid = new_request(p)
    reject(p, rid, 2, "ALLOW_BRAND", brand="Arla")
    assert p.profiles.get("prof_banquet").body.hard_exclusions.brands == ["Arla"]


def test_constraints_accumulate_across_attempts(env):
    p, _ = env
    rid = new_request(p)
    reject(p, rid, 2, "ALLOW_BRAND", brand="Arla")
    r = reject(p, rid, 3, "WRONG_PRIORITY", weights={"price": 1.0, "fulfilment": 0.0, "carbon": 0.0, "local": 0.0})
    assert r.attempt == 3
    assert "464179" in {c.lines[0].product_id for c in r.candidates}          # brand allowance still applies
    grosses = [c.totals.gross_cents for c in r.candidates]
    assert grosses[0] == min(grosses)                                          # price-only weights


def test_exclude_option_defaults_to_current_top(env):
    p, _ = env
    rid = new_request(p)
    top = p.result(rid).candidates[0].lines[0].product_id
    r = reject(p, rid, 3, "EXCLUDE_OPTION")
    assert top not in {c.lines[0].product_id for c in r.candidates}


def test_wrong_quantity_replaces_structured_request(env):
    p, _ = env
    rid = new_request(p)
    fixed = fx.STRUCTURED.model_copy(update={"quantity": Quantity(value=200, unit="L")}).model_dump(mode="json")
    r = reject(p, rid, 1, "WRONG_QUANTITY", structured=fixed)
    assert r.structured.quantity.value == 200
    assert "200 L" in r.chain[0].summary
    assert all(c.lines[0].qty_covered.value >= 200 for c in r.candidates)


def test_send_to_approval_never_loosens(env):
    p, _ = env
    rid = new_request(p)
    before = {c.lines[0].product_id: c.outcome_preview for c in p.result(rid).candidates}
    r = reject(p, rid, 4, "SEND_TO_APPROVAL")
    for c in r.candidates:
        assert c.outcome_preview != "AUTO"
        if before[c.lines[0].product_id] == "REJECT":
            assert c.outcome_preview == "REJECT"


def test_pick_other_drops_current_top(env):
    p, _ = env
    rid = new_request(p)
    top = p.result(rid).candidates[0].lines[0].product_id
    r = reject(p, rid, 4, "PICK_OTHER")
    assert top not in {c.lines[0].product_id for c in r.candidates}
    assert r.candidates[0].rank == 1 and r.candidates[0].badge == "RECOMMENDED"


def test_gate_node_rejects_reasons_that_would_loosen(env):
    p, _ = env
    rid = new_request(p)
    with pytest.raises(PipelineError) as e:
        p.reject(rid, 4, RejectNodeRequest(reason="TOO_STRICT"))
    assert e.value.code == "VALIDATION_ERROR"


def test_retry_limit_is_three_per_node(env):
    p, _ = env
    rid = new_request(p)
    for left in (2, 1, 0):
        r = reject(p, rid, 3, "PREFER_SINGLE_SKU")
        assert r.chain[2].retries_left == left
    assert r.chain[2].can_reject is False and r.chain[1].can_reject is True
    with pytest.raises(PipelineError) as e:
        p.reject(rid, 3, RejectNodeRequest(reason="PREFER_SINGLE_SKU"))
    assert e.value.code == "RETRY_LIMIT"


def test_unknown_request_is_not_found(env):
    p, _ = env
    with pytest.raises(PipelineError) as e:
        p.result("req_nope")
    assert e.value.code == "NOT_FOUND"


# ───── external agent requests ─────

def new_agent_request(p: Pipeline) -> str:
    body = CreateRequest(protocol_id="prot_hotel_ext_v1", input_mode="PANEL", structured=fx.STRUCTURED)
    return p.create_request("buy_hotel", "agent:agt_hotel_ext", body)


def test_agent_request_skips_node_1(env):
    p, _ = env
    r = p.result(new_agent_request(p))
    n1 = r.chain[0]
    assert n1.can_reject is False and n1.retries_left == 0 and n1.reject_options == []
    assert "agt_hotel_ext" in n1.summary and "not interpreted" in n1.summary
    assert "skipped" in n1.events[0].logic
    assert r.candidates and r.chain[1].can_reject is True


def test_agent_cannot_reject_node_1(env):
    p, _ = env
    rid = new_agent_request(p)
    with pytest.raises(PipelineError) as e:
        p.reject(rid, 1, RejectNodeRequest(reason="WRONG_QUANTITY"))
    assert e.value.code == "VALIDATION_ERROR"
    assert p.result(rid).attempt == 1


def test_agent_skipped_node_stays_skipped_after_rerun(env):
    p, _ = env
    rid = new_agent_request(p)
    r = reject(p, rid, 3, "PREFER_SINGLE_SKU")
    assert r.attempt == 2 and r.chain[0].can_reject is False


def test_human_request_node_1_is_still_rejectable(env):
    p, _ = env
    n1 = p.result(new_request(p)).chain[0]
    assert n1.can_reject is True and n1.summary.startswith("You need:")


def test_timeline_events_are_in_time_order(env):
    p, _ = env
    rid = new_request(p)
    t = p.timeline(rid)
    assert t.attempt == 1 and len(t.events) >= 4
    assert [e.at for e in t.events] == sorted(e.at for e in t.events)
