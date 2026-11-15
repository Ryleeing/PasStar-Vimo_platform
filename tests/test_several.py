"""Spotting a message that asks for several products, which comes in a later version."""
import pytest

from app.orchestrator.several import several_products


@pytest.mark.parametrize("text, products", [
    ("200 litres of milk and 20 litres of cream for Monday", ["milk", "cream"]),
    ("milk, butter and eggs tomorrow", ["milk", "butter", "eggs"]),
    ("100 l maitoa ja 10 l kermaa", ["milk", "cream"]),
    ("2 kg ost och 5 kg smör", ["cheese", "butter"]),
    ("ice cream and coffee", ["ice cream", "coffee"]),
])
def test_several_products_are_spotted(text, products):
    assert [i["product"] for i in several_products(text)] == products


@pytest.mark.parametrize("text", [
    "1000 litres of lactose-free milk by Monday",
    "milk for the cream sauce, Friday",          # one product, the other word is what it is for
    "voitko tilata 100 litraa maitoa",            # "voi" in "voitko" is not butter
    "milk powder and nothing else",
    "200 l milk and 300 l milk",                  # the same product twice is still one
])
def test_one_product_is_not_several(text):
    assert several_products(text) == []


def test_agents_are_asked_to_go_one_product_at_a_time():
    from app.channel import identity
    from app.orchestrator import agent_api
    r = agent_api.find_options(identity.caller("demo-hotel", None), "200 litres of milk and 20 litres of cream tomorrow")
    assert r["status"] == "ONE_PRODUCT_AT_A_TIME" and [p["product"] for p in r["products"]] == ["milk", "cream"]
    assert "next version" in r["message"]


def test_the_screens_get_the_products_and_a_form_for_the_first():
    from fastapi.testclient import TestClient
    from app.main import app
    r = TestClient(app).post("/v1/requests/parse", json={"text": "200 litres of milk and 20 litres of cream tomorrow"},
                             headers={"X-Demo-User": "usr_chef"}).json()
    assert [i["product"] for i in r["several"]] == ["milk", "cream"] and r["structured"]["category"] == "10400"
    one = TestClient(app).post("/v1/requests/parse", json={"text": "200 litres of milk tomorrow"},
                               headers={"X-Demo-User": "usr_chef"}).json()
    assert one["several"] == []
