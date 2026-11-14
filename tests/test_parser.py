"""Parser: LLM path (mocked OpenRouter), rule-based fallback, and the shared validation."""
import json
from datetime import date

import httpx
import pytest

from app import fixtures as fx
from app.orchestrator.llm import PRESETS, LlmClient, resolve_model, strict_schema
from app.orchestrator.parser import ParsedNeed, Parser, rule_parse

SATURDAY = date(2026, 11, 14)


def llm_returning(payload: dict | None, status: int = 200, seen: list | None = None) -> LlmClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        if payload is None:
            return httpx.Response(status, json={"error": {"message": "boom"}})
        return httpx.Response(status, json={"choices": [{"message": {"content": json.dumps(payload)}}]})
    return LlmClient(api_key="test-key", model="some/model", http=httpx.Client(transport=httpx.MockTransport(handler)))


def parser(llm: LlmClient) -> Parser:
    return Parser(llm=llm, today=lambda: SATURDAY)


def need(**kw) -> dict:
    base = {"category": "10400", "requirements": [{"kind": "CLAIM", "code": "FREE_FROM_LACTOSE"}],
            "quantity_value": 1000, "quantity_unit": "L", "delivery_date": "2026-11-16", "budget_eur": None,
            "substitution": None, "notes": None, "ambiguities": []}
    return {**base, **kw}


# ───── model selection ─────

def test_tiers_and_override(monkeypatch):
    monkeypatch.delenv("THE_PASS_LLM_MODEL", raising=False)
    assert resolve_model("test") == PRESETS["test"] and resolve_model("demo") == PRESETS["demo"]
    monkeypatch.setenv("THE_PASS_LLM_TIER", "demo")
    assert resolve_model() == PRESETS["demo"]
    monkeypatch.setenv("THE_PASS_LLM_MODEL", "mistralai/mistral-small-2603")
    assert resolve_model() == "mistralai/mistral-small-2603"
    monkeypatch.delenv("THE_PASS_LLM_MODEL")
    with pytest.raises(ValueError):
        resolve_model("premium")


def test_strict_schema_closes_every_object():
    s = strict_schema(ParsedNeed)
    assert s["additionalProperties"] is False and set(s["required"]) == set(s["properties"])
    for sub in s["$defs"].values():
        assert sub["additionalProperties"] is False


# ───── LLM path ─────

def test_llm_answer_is_used_and_request_is_structured():
    seen = []
    p = parser(llm_returning(need(), seen=seen))
    s, amb, _ = p.parse("1000 litres of lactose-free milk by Monday", None)
    assert (s.category, s.quantity.value, s.delivery_date) == ("10400", 1000, date(2026, 11, 16))
    assert [r.code for r in s.requirements] == ["FREE_FROM_LACTOSE"] and s.requirements[0].label == "Lactose-free"
    assert amb == [] and p.last_source == "llm:some/model"
    body = seen[0]
    assert body["model"] == "some/model" and body["response_format"]["json_schema"]["strict"] is True
    assert "2026-11-14" in body["messages"][1]["content"]


def test_llm_invented_codes_are_dropped_and_asked_about():
    p = parser(llm_returning(need(category="99999", requirements=[{"kind": "CLAIM", "code": "KETO"}])))
    s, amb, _ = p.parse("keto milk", None)
    fields = [a["field"] for a in amb]
    assert "category" in fields and "requirements" in fields
    assert s.requirements == []


def test_llm_past_date_becomes_a_question():
    s, amb, _ = parser(llm_returning(need(delivery_date="2026-11-01"))).parse("milk by 1 Nov", None)
    assert s.delivery_date > SATURDAY and any(a["field"] == "delivery_date" for a in amb)


@pytest.mark.parametrize("llm", [
    llm_returning(None, status=500),                       # provider error
    llm_returning({"not": "the schema"}),                  # invalid output
    LlmClient(api_key="", model="some/model"),             # no key configured
])
def test_falls_back_to_rules(llm):
    p = parser(llm)
    s, _, _ = p.parse("1000 litres of lactose-free milk by Monday", None)
    assert p.last_source == "rules"
    assert (s.category, s.quantity.value, s.delivery_date) == ("10400", 1000, date(2026, 11, 16))


# ───── rule-based parser ─────

@pytest.mark.parametrize("text, category, qty, unit, codes", [
    ("1000 litres of lactose-free milk by Monday", "10400", 1000, "L", {"FREE_FROM_LACTOSE"}),
    ("1 000 l laktoositonta maitoa maanantaina", "10400", 1000, "L", set()),
    ("50 kg organic quark, nut-free, tomorrow", "10430", 50, "kg", {"EU_ORGANIC_FARMING", "AN"}),
    ("2,5 l kermaa huomenna", "10401", 2.5, "L", set()),
])
def test_rule_parse(text, category, qty, unit, codes):
    n = rule_parse(text, SATURDAY)
    assert (n.category, n.quantity_value, n.quantity_unit) == (category, qty, unit)
    assert {r.code for r in n.requirements} >= codes


def test_rule_parse_finnish_lactose_free():
    assert {r.code for r in rule_parse("laktoositon maito 200 l", SATURDAY).requirements} == {"FREE_FROM_LACTOSE"}


def test_weekday_is_next_occurrence_never_today():
    assert rule_parse("milk 10 l by saturday", SATURDAY).delivery_date == "2026-11-21"


def test_missing_quantity_and_date_become_questions():
    p = parser(LlmClient(api_key="", model="m"))
    s, amb, _ = p.parse("lactose-free milk", None)
    assert {a["field"] for a in amb} >= {"quantity", "delivery_date"}


def test_profile_defaults_are_applied_and_reported():
    p = parser(LlmClient(api_key="", model="m"))
    s, _, applied = p.parse("lactose-free milk 100 l", fx.PROFILE)
    assert s.delivery_date == date(2026, 11, 16)            # lead_days = 2
    assert s.substitution == "SAME_CATEGORY"
    assert any("profile default" in a for a in applied)


# ───── protocol draft ─────

def test_draft_protocol_rules():
    p = parser(LlmClient(api_key="", model="m"))
    body, qs = p.draft_protocol("buy_hotel", "agt_hotel",
                                "Dairy only, lactose-free, automatic up to €1,000, never above €3,000, for 3 months")
    assert (body.auto_approve_limit_cents, body.max_order_cents) == (100_000, 300_000)
    assert body.allowed_categories == ["10400", "10401", "10405"]
    assert [r.code for r in body.hard_requirements] == ["FREE_FROM_LACTOSE"]
    assert body.approval_channel == "HOSTED" and body.approver == "usr_manager"
    assert {q["field"] for q in qs} >= {"approval_mode", "approver"}


def test_draft_protocol_llm_auto_above_max_is_capped():
    d = {"auto_approve_limit_eur": 5000, "max_order_eur": 2000, "allowed_categories": ["10400"],
         "hard_requirements": [], "approval_mode": "SELF", "valid_months": 24, "open_questions": []}
    body, qs = parser(llm_returning(d)).draft_protocol("buy_x", "agt_x", "whatever")
    assert body.auto_approve_limit_cents == body.max_order_cents == 200_000
    assert body.approver == "usr_owner" and (body.valid_until - SATURDAY).days <= 365
    assert any(q["field"] == "auto_approve_limit_cents" for q in qs)


# ───── HTTP route ─────

def test_parse_route_uses_real_parser(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.orchestrator import parser_api
    monkeypatch.setattr(parser_api, "llm", LlmClient(api_key="", model="m"))
    r = TestClient(app).post("/v1/requests/parse", json={"text": "200 l lactose-free milk tomorrow", "profile_id": "prof_banquet"})
    assert r.status_code == 200
    assert r.json()["structured"]["quantity"] == {"value": 200.0, "unit": "L"}
