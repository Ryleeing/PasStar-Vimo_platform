"""Step 0 for the bistro: it starts with no protocol; the owner drafts and signs one, Valio confirms, ordering starts."""
import pytest
from fastapi.testclient import TestClient

from app.channel import identity, seed
from app.errors import ApiError
from app.main import app
from app.orchestrator import agent_api

OWNER, VALIO = {"X-Demo-User": "usr_owner"}, {"X-Demo-User": "usr_valio"}
RULES = "Milk and cream. Order on its own up to 1,000 euros, never above 3,000."


@pytest.fixture
def client():
    seed.reset()                          # as the app starts and as Reset demo leaves it
    return TestClient(app)


def onboard(client):
    d = client.post("/v1/protocols/draft", json={"buyer_id": "buy_bistro", "agent_id": "agt_bistro",
                                                   "description": RULES}, headers=OWNER).json()
    assert client.post(f"/v1/protocols/{d['id']}/sign", headers=OWNER).status_code == 200
    return d


def test_bistro_starts_without_a_protocol(client):
    r = client.get("/v1/agents/agt_bistro/protocol", headers=OWNER)
    assert r.json()["error"]["code"] == "PROTOCOL_INVALID"
    assert client.get("/v1/agents/agt_hotel/protocol", headers={"X-Demo-User": "usr_manager"}).status_code == 200


def test_the_agent_can_do_nothing_before_step_0(client):
    with pytest.raises(ApiError) as e:
        agent_api.get_mandate(identity.caller("demo-bistro", None))
    assert e.value.code == "PROTOCOL_INVALID"


def test_one_person_business_drafts_as_self_approval_without_asking(client):
    d = client.post("/v1/protocols/draft", json={"buyer_id": "buy_bistro", "agent_id": "agt_bistro",
                                                   "description": RULES}, headers=OWNER).json()
    assert d["body"]["approval_mode"] == "SELF" and d["body"]["approver"] == "usr_owner"
    assert not {"approval_mode", "approver"} & {q["field"] for q in d["open_questions"]}


def test_signed_protocol_waits_in_valios_queue_then_activates(client):
    d = onboard(client)
    pending = client.get("/v1/protocols?status=PENDING_VALIO_CONFIRMATION", headers=VALIO).json()
    assert [p["id"] for p in pending] == [d["id"]]
    assert client.post(f"/v1/protocols/{d['id']}/confirm", headers=VALIO).status_code == 200
    active = client.get("/v1/agents/agt_bistro/protocol", headers=OWNER).json()
    assert active["id"] == d["id"] and active["status"] == "ACTIVE"
    assert agent_api.get_mandate(identity.caller("demo-bistro", None))["protocol"]["id"] == d["id"]


def test_buyers_list_only_their_own_protocols(client):
    onboard(client)
    mine = client.get("/v1/protocols", headers=OWNER).json()
    assert {p["body"]["buyer_id"] for p in mine} == {"buy_bistro"}
    assert client.get("/v1/protocols", headers={"X-Agent-Key": "demo-hotel"}).status_code == 403


def test_the_draft_can_be_edited_before_signing_but_not_after(client):
    d = client.post("/v1/protocols/draft", json={"buyer_id": "buy_bistro", "agent_id": "agt_bistro",
                                                   "description": RULES}, headers=OWNER).json()
    body = {**d["body"], "auto_approve_limit_cents": 50_000, "allowed_categories": ["10400"]}
    edited = client.put(f"/v1/protocols/{d['id']}", json={"body": body}, headers=OWNER).json()
    assert edited["body"]["auto_approve_limit_cents"] == 50_000 and edited["body"]["allowed_categories"] == ["10400"]
    assert client.post(f"/v1/protocols/{d['id']}/sign", headers=OWNER).status_code == 200
    late = client.put(f"/v1/protocols/{d['id']}", json={"body": {**body, "max_order_cents": 900_000}}, headers=OWNER)
    assert late.json()["error"]["code"] == "PROTOCOL_INVALID"
