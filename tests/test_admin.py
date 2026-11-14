"""Admin routes answer only on the local machine, never through a tunnel."""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_local_reset_works(client):
    assert client.post("/v1/admin/reset").json() == {"ok": True}


@pytest.mark.parametrize("header", [{"Cf-Connecting-Ip": "203.0.113.7"}, {"X-Forwarded-For": "203.0.113.7"}])
def test_reset_through_a_tunnel_is_refused_and_changes_nothing(client, header):
    db.run("UPDATE agents SET api_key_hash = 'rotated' WHERE id = 'agt_hotel'")
    r = client.post("/v1/admin/reset", headers=header)
    assert r.status_code == 403 and r.json()["error"]["code"] == "OUT_OF_SCOPE"
    assert db.one("SELECT api_key_hash FROM agents WHERE id = 'agt_hotel'")["api_key_hash"] == "rotated"


def test_faults_through_a_tunnel_are_refused(client):
    h = {"Cf-Connecting-Ip": "203.0.113.7"}
    assert client.get("/v1/admin/faults", headers=h).status_code == 403
    assert client.put("/v1/admin/faults/TIMEOUT", json={"enabled": True}, headers=h).status_code == 403


def test_people_cannot_sign_in_through_a_tunnel(client):
    tunnel = {"Cf-Connecting-Ip": "203.0.113.7", "X-Demo-User": "usr_manager"}
    r = client.get("/v1/approvals", headers=tunnel)
    assert r.status_code == 403 and r.json()["error"]["code"] == "OUT_OF_SCOPE"
    assert client.get("/v1/approvals", headers={"X-Demo-User": "usr_manager"}).status_code == 200


def test_agents_still_work_through_a_tunnel(client):
    r = client.get("/v1/agents/agt_hotel/protocol", headers={"Cf-Connecting-Ip": "203.0.113.7", "X-Agent-Key": "demo-hotel"})
    assert r.status_code == 200
