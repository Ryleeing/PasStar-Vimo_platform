"""BE3 fake interfaces return values that match the internal.py signatures."""
from app import fixtures as fx
from app.internal import DisclosablePack
from app.orchestrator.fakes import FakeExplain, FakeParser, FakePipeline
from app.schemas import Ambiguity, OpenQuestion, ProtocolBody, StructuredRequest

parser_api = FakeParser()
explain_api = FakeExplain()
pipeline_api = FakePipeline()


def test_parse_returns_structured_ambiguities_and_profile_items():
    structured, ambiguities, applied = parser_api.parse("1000 litres of lactose-free milk by Monday", fx.PROFILE)
    assert isinstance(structured, StructuredRequest)
    assert all(Ambiguity(**a) for a in ambiguities)
    assert applied


def test_parse_without_profile_applies_nothing():
    _, _, applied = parser_api.parse("milk", None)
    assert applied == []


def test_draft_protocol_keeps_buyer_and_agent():
    body, questions = parser_api.draft_protocol("buy_hospital", "agt_hospital", "Dairy up to €1,000 automatically")
    assert isinstance(body, ProtocolBody)
    assert (body.buyer_id, body.agent_id) == ("buy_hospital", "agt_hospital")
    assert all(OpenQuestion(**q) for q in questions)


def test_pipeline_run_increments_attempt():
    pipeline_api.run("req_fake_1")
    assert pipeline_api.result("req_fake_1").attempt == 1
    pipeline_api.run("req_fake_1", from_node=2, constraints=[{"reason": "ALLOW_BRAND", "params": {"brand": "Arla"}}])
    r = pipeline_api.result("req_fake_1")
    assert r.attempt == 2 and all(n.attempt == 2 for n in r.chain)


def test_explain_returns_text():
    pack = DisclosablePack(data={}, max_sensitivity="L1")
    assert explain_api.node_summary(2, pack)
    assert explain_api.candidate_explanation(pack)
