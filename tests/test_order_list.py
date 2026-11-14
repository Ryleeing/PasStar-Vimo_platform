"""GET /v1/orders: a buyer's orders, newest first, whoever placed them (a person or an agent)."""
from fastapi.testclient import TestClient

from app.channel import identity
from app.main import app
from app.orchestrator import agent_api

CHEF, MANAGER, OWNER, VALIO = ({"X-Demo-User": u} for u in ("usr_chef", "usr_manager", "usr_owner", "usr_valio"))


def agent_order(key, text):
    who = identity.caller(key, None)
    r = agent_api.find_options(who, text)
    return agent_api.place_order(who, r["request_id"], r["options"][0]["option_id"])


def test_orders_placed_by_an_agent_show_up_for_the_buyers_people():
    placed = agent_order("demo-hotel", "200 litres of lactose-free milk tomorrow")
    c = TestClient(app)
    rows = c.get("/v1/orders", headers=CHEF).json()
    assert [r["order"]["id"] for r in rows] == [placed["order_id"]]
    row = rows[0]
    assert row["placed_by"] == {"kind": "AGENT", "id": "agt_hotel", "name": "Hotel purchasing agent", "agent_kind": "CHATGPT"}
    assert row["quantity"]["value"] == 200 and row["total_gross_cents"] > 0 and row["product"]
    assert c.get("/v1/orders", headers=MANAGER).json() == rows


def test_newest_first_and_only_your_own_buyer():
    first = agent_order("demo-hotel", "200 litres of lactose-free milk tomorrow")
    second = agent_order("demo-hotel", "300 litres of lactose-free milk tomorrow")
    agent_order("demo-bistro", "100 litres of milk tomorrow")
    c = TestClient(app)
    hotel = [r["order"]["id"] for r in c.get("/v1/orders", headers=CHEF).json()]
    assert hotel == [second["order_id"], first["order_id"]]
    bistro = c.get("/v1/orders", headers=OWNER).json()
    assert len(bistro) == 1 and bistro[0]["buyer_id"] == "buy_bistro"
    assert len(c.get("/v1/orders", headers=VALIO).json()) == 3


def test_agents_do_not_list_orders():
    assert TestClient(app).get("/v1/orders", headers={"X-Agent-Key": "demo-hotel"}).status_code == 403
