"""The evaluation runner's judging and metrics, plus a small in-process run (rule-based parser)."""
import pathlib
import sys
from datetime import date

import pytest
import yaml
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import runner  # noqa: E402
from app.main import app  # noqa: E402
from app.orchestrator import parser_api  # noqa: E402
from app.orchestrator.llm import LlmClient  # noqa: E402
from pass_client import PassClient  # noqa: E402

SATURDAY = date(2026, 11, 14)
EXPECT = {"category": "10400", "requirements": ["FREE_FROM_LACTOSE"], "quantity": {"value": 1000, "unit": "L"},
          "delivery": "next_monday", "final": ["PLACED"]}
GOOD = {"category": "10400", "requirements": [{"code": "FREE_FROM_LACTOSE"}],
        "quantity": {"value": 1000.0, "unit": "L"}, "delivery_date": "2026-11-16"}


def result(status, fault=None, understood=()):
    return runner.judge(runner.RunResult("S", 0, "t", fault, 0, status), EXPECT, list(understood))


def test_expected_dates():
    assert runner.expected_date("next_monday", SATURDAY) == date(2026, 11, 16)
    assert runner.expected_date("next_saturday", SATURDAY) == date(2026, 11, 21)
    assert runner.expected_date("tomorrow", SATURDAY) == date(2026, 11, 15)
    assert runner.expected_date("in_3_days", SATURDAY) == date(2026, 11, 17)


def test_understanding_checks_every_field():
    assert runner.check_understanding(EXPECT, GOOD, SATURDAY) == []
    bad = {**GOOD, "requirements": [{"code": "FREE_FROM_LACTOSE"}, {"code": "LOW_FAT"}], "delivery_date": "2026-11-17"}
    issues = runner.check_understanding(EXPECT, bad, SATURDAY)
    assert any("requirements" in i for i in issues) and any("delivery" in i for i in issues)


def test_placed_with_misunderstanding_is_a_silent_wrong_order():
    r = result("PLACED", understood=["quantity 1 L != 1000 L"])
    assert not r.passed and r.silent_wrong


def test_placed_under_partial_stock_is_silent_wrong_but_approval_is_fine():
    assert result("PLACED", fault="PARTIAL_STOCK").silent_wrong
    ok = result("PENDING_BUYER_APPROVAL", fault="PARTIAL_STOCK")
    assert ok.passed and not ok.false_block


def test_stopping_a_good_request_is_a_false_block():
    r = result("NEEDS_INPUT")
    assert r.false_block and not r.passed and not r.silent_wrong


def test_pass_hat_k_needs_every_run_to_pass():
    rs = [result("PLACED"), result("PLACED"), result("NEEDS_INPUT")]
    for i, r in enumerate(rs):
        r.run = i
    s = runner.summarize(rs)
    assert s["pass@1"] == round(2 / 3, 3) and s["pass^k"] == 0.0 and s["false_block_rate"] == round(1 / 3, 3)


@pytest.fixture
def rules_only(monkeypatch):
    monkeypatch.setattr(parser_api, "llm", LlmClient(api_key="", model="m"))


def test_in_process_run_on_the_mock_server(rules_only):
    scenarios = yaml.safe_load(runner.SCENARIOS.read_text())["scenarios"]
    picked = [s for s in scenarios if s["id"] in ("S1-milk-auto", "S4-missing-quantity")]
    results = runner.run_all(PassClient(http=TestClient(app)), picked, [None], k=1, poll_every_s=0, quiet=True)
    assert len(results) == 10
    by = {(r.scenario, r.phrasing_index): r for r in results}
    assert by[("S1-milk-auto", 0)].passed                       # English, rule parser handles it
    assert all(by[("S4-missing-quantity", i)].status == "NEEDS_INPUT" for i in range(5))
    assert runner.summarize(results)["silent_wrong_orders"] == 0
