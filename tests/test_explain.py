"""LlmExplain: one light-model call per attempt, template fallback for anything missing or unusable."""
import json
import sqlite3
from pathlib import Path

import httpx

from app import fixtures as fx
from app.internal import DisclosablePack
from app.orchestrator.explain import LlmExplain, TemplateExplain
from app.orchestrator.llm import LlmClient
from app.orchestrator.pipeline import Deps, Pipeline
from app.orchestrator.profiles import ProfileRepo
from app.orchestrator.stubs import (
    StubCards, StubCatalog, StubDisclosure, StubGate, StubProtocols, StubUnits, StubValio,
)
from app.schemas import CreateRequest, RejectNodeRequest

SCHEMA = Path(__file__).resolve().parents[1] / "backend" / "app" / "schema.sql"


def explain_with(reply, calls: list) -> LlmExplain:
    """reply(body) -> the model's JSON answer as a dict, or None for an HTTP 500."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        answer = reply(body)
        if answer is None:
            return httpx.Response(500, json={"error": {"message": "down"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})
    return LlmExplain(LlmClient(task="explain", api_key="k", http=httpx.Client(transport=httpx.MockTransport(handler))))


def everything(body: dict) -> dict:
    data = json.loads(body["messages"][1]["content"])
    return {"node_summaries": [{"id": k, "text": f"LLM {k}"} for k in data["nodes"]],
            "candidate_explanations": [{"id": k, "text": f"LLM {k}"} for k in data["candidates"]]}


def pipeline(explain) -> Pipeline:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA.read_text())
    conn.execute("INSERT INTO buyers (id, name) VALUES ('buy_hotel', 'Hotel Helsinki Central')")
    ProfileRepo(conn).create("buy_hotel", fx.PROFILE.body, profile_id="prof_banquet")
    deps = Deps(catalog=StubCatalog(), units=StubUnits(), valio=StubValio(), cards=StubCards(),
                disclosure=StubDisclosure(), protocols=StubProtocols(), gate=StubGate(), explain=explain)
    return Pipeline(conn, deps, clock=lambda: fx.NOW)


def new_request(p: Pipeline) -> str:
    return p.create_request("buy_hotel", "usr_chef", CreateRequest(
        protocol_id="prot_hotel_v1", profile_id="prof_banquet", input_mode="PANEL", structured=fx.STRUCTURED))


def test_one_llm_call_per_attempt_writes_every_text():
    calls = []
    p = pipeline(explain_with(everything, calls))
    r = p.result(new_request(p))
    assert len(calls) == 1
    assert [n.summary for n in r.chain] == ["LLM node_1", "LLM node_2", "LLM node_3", "LLM node_4"]
    assert all(c.explanation.startswith("LLM cand_") for c in r.candidates + r.blocked)
    assert calls[0]["model"] == "google/gemini-2.5-flash-lite" and calls[0]["temperature"] == 0


def test_rerun_explains_only_the_new_nodes():
    calls = []
    p = pipeline(explain_with(everything, calls))
    rid = new_request(p)
    p.reject(rid, 3, RejectNodeRequest(reason="PREFER_SINGLE_SKU"))
    assert len(calls) == 2
    assert sorted(json.loads(calls[1]["messages"][1]["content"])["nodes"]) == ["node_3", "node_4"]
    r = p.result(rid)
    assert r.chain[0].summary == "LLM node_1"            # copied from attempt 1, not rewritten


def test_llm_failure_falls_back_to_templates():
    calls = []
    p = pipeline(explain_with(lambda body: None, calls))
    r = p.result(new_request(p))
    assert len(calls) == 1 and r.chain[0].summary.startswith("You need:")
    assert all(c.explanation for c in r.candidates + r.blocked)


def test_missing_and_overlong_sentences_use_the_template():
    def partial(body):
        return {"node_summaries": [{"id": "node_2", "text": "x" * 500}, {"id": "node_9", "text": "bogus"}],
                "candidate_explanations": [{"id": "cand_0", "text": "LLM first"}]}
    p = pipeline(explain_with(partial, []))
    r = p.result(new_request(p))
    assert r.chain[1].summary.startswith("Kept ")                 # too long -> template
    assert r.chain[0].summary.startswith("You need:")             # missing -> template
    first, *rest = r.candidates
    assert first.explanation == "LLM first" and not any(c.explanation.startswith("LLM") for c in rest)


def test_only_the_disclosable_pack_is_sent():
    calls = []
    explain = explain_with(everything, calls)
    pack = DisclosablePack(data={"lines": [{"name": "Milk"}], "badge": "CHEAPEST"}, max_sensitivity="L1")
    explain.explain_attempt({}, [pack])
    sent = json.loads(calls[0]["messages"][1]["content"])
    assert sent == {"nodes": {}, "candidates": {"cand_0": pack.data}}


def test_template_explain_attempt_matches_single_calls():
    t = TemplateExplain()
    pack = DisclosablePack(data={"kept": 4, "blocked": 1, "dropped": 2}, max_sensitivity="L1")
    summaries, _ = t.explain_attempt({2: pack}, [])
    assert summaries[2] == t.node_summary(2, pack)
