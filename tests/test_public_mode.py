"""Public deployment (PASSTAR_PUBLIC=1): people need the demo passcode's session; agents still use their keys."""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.channel import identity, seed
from app.errors import ApiError
from app.main import app

PROXY = {"X-Forwarded-For": "203.0.113.7"}         # every visitor of a deployment comes through the platform proxy


@pytest.fixture
def public(monkeypatch):
    monkeypatch.setenv("PASSTAR_PUBLIC", "1")
    monkeypatch.setenv("PASSTAR_DEMO_PASSCODE", "open-sesame")
    return TestClient(app)


def token(c):
    return c.post("/v1/session", json={"passcode": "open-sesame"}, headers=PROXY).json()["token"]


def test_without_the_passcode_people_are_refused(public):
    r = public.get("/v1/agents", headers={**PROXY, "X-Demo-User": "usr_owner"})
    assert r.status_code == 403 and "passcode" in r.json()["error"]["message"]
    assert public.get("/v1/session", headers=PROXY).json() == {"public": True, "signed_in": False}


def test_wrong_passcode_gets_no_token(public):
    assert public.post("/v1/session", json={"passcode": "guess"}, headers=PROXY).status_code == 403


def test_with_the_session_people_can_use_the_screens(public):
    t = token(public)
    h = {**PROXY, "X-Demo-User": "usr_owner", "X-Demo-Session": t}
    assert public.get("/v1/session", headers=h).json()["signed_in"] is True
    assert public.get("/v1/agents", headers=h).status_code == 200


def test_a_session_may_reset_the_demo_and_demo_keys_are_random(public):
    t = token(public)
    assert public.post("/v1/admin/reset", headers={**PROXY, "X-Demo-Session": t}).status_code == 200
    with pytest.raises(ApiError):
        identity.caller("demo-hotel", None)              # the keys written in the code do not work in public
    assert public.post("/v1/admin/reset", headers=PROXY).status_code == 403


def test_agents_still_use_their_keys(public, monkeypatch):
    monkeypatch.delenv("PASSTAR_PUBLIC")
    seed.reset()                                          # demo keys again, then go public
    monkeypatch.setenv("PASSTAR_PUBLIC", "1")
    assert public.get("/v1/agents/agt_hotel/protocol", headers={**PROXY, "X-Agent-Key": "demo-hotel"}).status_code == 200
    assert db.one("SELECT 1 FROM agents") is not None


def test_locally_nothing_changes():
    c = TestClient(app)
    assert c.get("/v1/session").json() == {"public": False, "signed_in": True}
    assert c.get("/v1/agents", headers={"X-Demo-User": "usr_owner"}).status_code == 200
