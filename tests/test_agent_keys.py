"""External agents and their keys: listing, issuing (shown once), revoking, and who may do it."""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.channel import identity
from app.errors import ApiError
from app.main import app

MANAGER, CHEF, VALIO = ({"X-Demo-User": u} for u in ("usr_manager", "usr_chef", "usr_valio"))


@pytest.fixture
def client():
    return TestClient(app)


def test_a_buyer_lists_its_own_agents_with_kind_connection_and_protocol(client):
    agents = {a["agent_id"]: a for a in client.get("/v1/agents", headers=MANAGER).json()}
    assert set(agents) == {"agt_hotel", "agt_hotel_ext", "agt_rogue"}
    hotel = agents["agt_hotel"]
    assert hotel["kind"] == "CHATGPT" and hotel["connection"] == "MCP" and hotel["protocol_id"] == "prot_hotel_v1"
    assert hotel["has_key"] and hotel["key_hint"] == "demo-hot"
    assert agents["agt_hotel_ext"]["connection"] == "REST"
    rogue = agents["agt_rogue"]
    assert rogue["protocol_id"] is None and rogue["connection"] == "NONE"


def test_valio_sees_every_buyers_agents(client):
    assert {a["buyer_id"] for a in client.get("/v1/agents", headers=VALIO).json()} == {"buy_hotel", "buy_hospital", "buy_bistro"}


def test_agents_cannot_list_agents(client):
    assert client.get("/v1/agents", headers={"X-Agent-Key": "demo-hotel"}).status_code == 403


def test_issuing_a_key_shows_it_once_and_retires_the_old_one(client):
    r = client.post("/v1/agents/agt_hotel/key", json={"kind": "CLAUDE"}, headers=MANAGER).json()
    assert r["key"].startswith("tp_") and r["key_hint"] == r["key"][:8] and r["protocol_id"] == "prot_hotel_v1"
    assert identity.caller(r["key"], None).agent_id == "agt_hotel"
    with pytest.raises(ApiError):
        identity.caller("demo-hotel", None)
    after = client.get("/v1/agents/agt_hotel", headers=MANAGER).json()
    assert after["kind"] == "CLAUDE" and after["key_hint"] == r["key_hint"] and "key" not in after
    assert r["key"] not in str(db.all("SELECT * FROM agents")) and r["key"] not in str(db.all("SELECT * FROM audit_log"))


def test_only_the_protocol_approver_manages_keys(client):
    assert client.post("/v1/agents/agt_hotel/key", json={"kind": "CHATGPT"}, headers=CHEF).json()["error"]["code"] == "NOT_APPROVER"
    assert client.post("/v1/agents/agt_hotel/key", json={"kind": "CHATGPT"}, headers=VALIO).json()["error"]["code"] == "NOT_APPROVER"


def test_no_key_before_step_0(client):
    r = client.post("/v1/agents/agt_rogue/key", json={"kind": "CHATGPT"}, headers=MANAGER)
    assert r.json()["error"]["code"] == "PROTOCOL_INVALID"


def test_revoked_key_stops_working_at_once(client):
    client.post("/v1/agents/agt_hotel/revoke", headers=MANAGER)
    with pytest.raises(ApiError):
        identity.caller("demo-hotel", None)
    a = client.get("/v1/agents/agt_hotel", headers=MANAGER).json()
    assert a["has_key"] is False and a["key_hint"] is None


def test_using_a_key_updates_last_used(client):
    assert client.get("/v1/agents/agt_hotel", headers=MANAGER).json()["last_used_at"] is None
    identity.caller("demo-hotel", None)
    assert client.get("/v1/agents/agt_hotel", headers=MANAGER).json()["last_used_at"] is not None


def test_key_changes_are_audited(client):
    client.post("/v1/agents/agt_hotel/key", json={"kind": "CHATGPT"}, headers=MANAGER)
    client.post("/v1/agents/agt_hotel/revoke", headers=MANAGER)
    events = [r["event"] for r in db.all("SELECT event FROM audit_log ORDER BY seq")]
    assert "AGENT_KEY_ISSUED" in events and "AGENT_KEY_REVOKED" in events
