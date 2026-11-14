"""Screens and agents find the protocol through the agent, so a raised limit (a new version) is picked up at once."""
import pytest
from fastapi.testclient import TestClient

from app.channel import identity
from app.main import app
from app.orchestrator import agent_api

OWNER, VALIO = {"X-Demo-User": "usr_owner"}, {"X-Demo-User": "usr_valio"}


@pytest.fixture
def client():
    return TestClient(app)


def raise_bistro_limit(client, cents):
    diff = client.post("/v1/protocols/prot_bistro_v1/amend", json={"changes": {"auto_approve_limit_cents": cents}},
                       headers=OWNER).json()
    r = client.post(f"/v1/approvals/{diff['approval_id']}/decision", json={"decision": "APPROVE"}, headers=OWNER)
    assert r.status_code == 200, r.text
    assert client.post("/v1/protocols/prot_bistro_v2/confirm", headers=VALIO).status_code == 200


def test_starts_with_the_seeded_version(client):
    p = client.get("/v1/agents/agt_bistro/protocol", headers=OWNER).json()
    assert p["id"] == "prot_bistro_v1" and p["status"] == "ACTIVE"


def test_raised_limit_is_the_new_active_protocol_for_screens_and_agents(client):
    raise_bistro_limit(client, 200_000)
    p = client.get("/v1/agents/agt_bistro/protocol", headers=OWNER).json()
    assert p["id"] == "prot_bistro_v2" and p["body"]["auto_approve_limit_cents"] == 200_000
    m = agent_api.get_mandate(identity.caller("demo-bistro", None))
    assert m["protocol"]["id"] == "prot_bistro_v2" and m["protocol"]["auto_order_up_to_eur"] == 2000


def test_requests_under_the_new_version_work(client):
    raise_bistro_limit(client, 200_000)
    pid = client.get("/v1/agents/agt_bistro/protocol", headers=OWNER).json()["id"]
    body = {"protocol_id": pid, "input_mode": "PANEL", "structured": {
        "category": "10400", "quantity": {"value": 200, "unit": "L"}, "delivery_date": "2030-01-07"}}
    assert client.post("/v1/requests", json=body, headers=OWNER).status_code == 200


def test_other_buyers_cannot_look_up_the_agent(client):
    assert client.get("/v1/agents/agt_bistro/protocol", headers={"X-Demo-User": "usr_chef"}).status_code == 404
    assert client.get("/v1/agents/agt_bistro/protocol", headers=VALIO).status_code == 200


def test_an_agent_sees_only_its_own(client):
    assert client.get("/v1/agents/agt_bistro/protocol", headers={"X-Agent-Key": "demo-bistro"}).status_code == 200
    assert client.get("/v1/agents/agt_bistro/protocol", headers={"X-Agent-Key": "demo-hotel"}).status_code == 404
