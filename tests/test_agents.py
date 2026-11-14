"""Agent scripts against the mock server, in-process (no network)."""
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "agent"))

import buyer_agent  # noqa: E402
import plain_client  # noqa: E402
import rogue_agent  # noqa: E402
from app.main import app  # noqa: E402
from pass_client import PassClient  # noqa: E402

FAST = {"poll_every_s": 0}


@pytest.fixture
def client():
    return PassClient(http=TestClient(app))


def test_plain_client_hosted_protocol_places_order(client):
    out = plain_client.run(client, "prot_hotel_v1", plain_client.DEFAULT_REQUEST, **FAST)
    assert out.status == "PLACED"
    assert [s.what for s in out.steps][:3] == ["Sent structured request", "Chose the top-ranked candidate", "Got a signed quote"]


def test_plain_client_external_protocol_gets_buyer_authorization(client):
    out = plain_client.run(client, "prot_hotel_ext_v1", plain_client.DEFAULT_REQUEST, **FAST)
    whats = [s.what for s in out.steps]
    assert "Needs buyer authorization" in whats and "Buyer system signed an authorization" in whats
    assert out.status == "PLACED" and out.order["authorized_by"] == "usr_manager"


def test_external_flow_with_a_bad_signature_stops_with_error(client):
    out = plain_client.run(client, "prot_hotel_ext_v1", plain_client.DEFAULT_REQUEST,
                           approve=lambda order: rogue_agent.FORGED_JWS, **FAST)
    assert out.status == "ERROR AUTHORIZATION_INVALID"


def test_buyer_agent_prefers_auto_candidate(client):
    out = buyer_agent.run(client, "1000 litres of lactose-free milk by Monday", "prot_hotel_v1", **FAST)
    chose = next(s for s in out.steps if s.what == "Chose a candidate")
    assert "expected AUTO" in chose.detail
    assert out.status == "PLACED"


def test_choose_falls_back_to_top_rank_when_nothing_is_auto():
    cands = [{"id": "b", "rank": 2, "selectable": True, "outcome_preview": "BUYER_APPROVAL"},
             {"id": "a", "rank": 1, "selectable": True, "outcome_preview": "BUYER_APPROVAL"},
             {"id": "x", "rank": None, "selectable": False, "outcome_preview": "REJECT"}]
    assert buyer_agent.choose(cands)["id"] == "a"
    assert buyer_agent.choose([cands[2]]) is None


def test_requery_never_touches_hard_requirements():
    s = {"category": "10400", "requirements": [{"kind": "CLAIM", "code": "FREE_FROM_LACTOSE"}],
         "delivery_date": "2026-11-16", "substitution": "NONE"}
    changed, _ = buyer_agent.relax_for_requery(s)
    assert changed["requirements"] == s["requirements"] and changed["substitution"] == "SAME_CATEGORY"
    changed, why = buyer_agent.relax_for_requery(changed)
    assert changed["delivery_date"] == "2026-11-17" and "delivery" in why


def test_rogue_agent_report_runs_every_probe(client):
    results = rogue_agent.run(client)
    assert len(results) == len(rogue_agent.PROBES)
    by_name = {r.probe.name: r for r in results}
    # Already enforced by the mock server:
    assert by_name["Forge a buyer authorization"].blocked
    assert by_name["Approve its own order as the requester"].blocked
