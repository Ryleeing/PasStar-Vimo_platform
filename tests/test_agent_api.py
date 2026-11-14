"""The agent-facing operations behind the MCP tools: compact results, questions first, no self-approval."""
import json

import pytest

from app.channel import identity
from app.errors import ApiError
from app.orchestrator import agent_api as api


@pytest.fixture
def hotel():
    return identity.caller("demo-hotel", None)


def test_mandate_names_the_buyer_limits_and_how_to_read(hotel):
    m = api.get_mandate(hotel)
    assert m["buyer"]["id"] == "buy_hotel" and m["protocol"]["id"] == "prot_hotel_v1"
    assert m["protocol"]["auto_order_up_to_eur"] < m["protocol"]["max_order_eur"]
    assert set(m["how_to_read"]["trust_grades"]) == {"A", "B", "C", "D"}
    assert any("approve" in r for r in m["rules"])


def test_agent_without_protocol_is_refused():
    with pytest.raises(ApiError) as e:
        api.get_mandate(identity.caller("demo-rogue", None))
    assert e.value.code == "PROTOCOL_INVALID"


def test_people_cannot_use_agent_operations():
    with pytest.raises(ApiError):
        api.get_mandate(identity.caller(None, "usr_chef"))


def test_missing_quantity_asks_before_searching(hotel):
    r = api.find_options(hotel, "lactose-free milk tomorrow")
    assert r["status"] == "NEEDS_INPUT"
    assert [q["field"] for q in r["questions"]] == ["quantity"]
    assert "request_id" not in r


def test_answers_complete_the_request(hotel):
    r = api.find_options(hotel, "lactose-free milk tomorrow", answers={"quantity": "200 litres"})
    assert r["status"] == "OPTIONS" and r["understood"]["quantity"] == "200 L"
    top = r["options"][0]
    assert top["rank"] == 1 and top["total_eur"] > 0
    assert top["if_chosen"]["outcome"] in {"AUTO", "BUYER_APPROVAL", "VALIO_REVIEW", "REJECT"}
    assert set(top["reliability"]) == {"overall", "evidence", "match", "fulfilment", "compliance", "how_to_improve"}
    assert all({"need", "status", "grade"} <= set(q) for q in top["requirements"])


def test_agent_text_is_parsed_in_node_1(hotel):
    from app.orchestrator import service
    r = api.find_options(hotel, "200 litres of lactose-free milk tomorrow")
    n1 = service.get_request(r["request_id"], hotel)["chain"][0]
    assert "passed on" in n1["summary"] and "not interpreted" not in n1["summary"]


def test_results_are_compact(hotel):
    r = api.find_options(hotel, "200 litres of lactose-free milk tomorrow")
    assert len(json.dumps(r)) < 12_000
    assert "chain" not in r and "cards" not in r["options"][0]


def test_adjust_by_price_puts_the_cheapest_first(hotel):
    rid = api.find_options(hotel, "200 litres of lactose-free milk tomorrow")["request_id"]
    r = api.adjust_options(hotel, rid, "prioritise", "price")
    totals = [o["total_eur"] for o in r["options"]]
    assert r["attempt"] == 2 and totals[0] == min(totals)


def test_adjust_rejects_unknown_changes(hotel):
    rid = api.find_options(hotel, "200 litres of lactose-free milk tomorrow")["request_id"]
    with pytest.raises(ApiError):
        api.adjust_options(hotel, rid, "ignore_protocol")


def _place(who, text):
    r = api.find_options(who, text)
    option = next(o for o in r["options"] if o["if_chosen"]["outcome"] != "REJECT")
    return option, api.place_order(who, r["request_id"], option["option_id"])


def test_small_order_is_placed(hotel):
    option, o = _place(hotel, "200 litres of lactose-free milk tomorrow")
    if option["if_chosen"]["outcome"] == "AUTO":
        assert o["status"] == "PLACED"
    assert o["total_eur"] == option["total_eur"]


def test_big_order_is_held_and_names_the_approver(hotel):
    option, o = _place(hotel, "2000 litres of lactose-free milk tomorrow")
    assert option["if_chosen"]["outcome"] == "BUYER_APPROVAL"
    assert o["status"] == "PENDING_BUYER_APPROVAL"
    mandate = api.get_mandate(hotel)
    assert mandate["protocol"]["approver"] in o["next_step"]
    assert api.order_status(hotel, o["order_id"])["status"] == "PENDING_BUYER_APPROVAL"


def test_placing_twice_returns_the_same_order(hotel):
    r = api.find_options(hotel, "200 litres of lactose-free milk tomorrow")
    oid = r["options"][0]["option_id"]
    first = api.place_order(hotel, r["request_id"], oid)
    assert api.place_order(hotel, r["request_id"], oid)["order_id"] == first["order_id"]


def test_another_buyers_agent_cannot_see_the_request(hotel):
    rid = api.find_options(hotel, "200 litres of lactose-free milk tomorrow")["request_id"]
    with pytest.raises(ApiError):
        api.get_options(identity.caller("demo-hospital", None), rid)


def test_options_say_where_each_fact_came_from(hotel):
    m = api.get_mandate(hotel)
    top = api.find_options(hotel, "200 litres of lactose-free milk tomorrow")["options"][0]
    known = set(m["how_to_read"]["sources"])
    assert {top["sources"]["price"], top["sources"]["deliverable"], *top["sources"]["pack_size"]} <= known
    assert all(r["source"] in known and r["grade"] in "ABCD" for r in top["requirements"])
    assert top["if_chosen"]["decided_by"] == "SIGNED_PROTOCOL"
    assert top["sources"]["price"] == "DEMO_DATA"          # tests run on BE3's stubs, and must say so
    assert all({"kind", "text"} == set(t) for t in top["tips"])


def test_sensitivity_is_stated_and_nothing_above_l1_is_returned(hotel):
    m = api.get_mandate(hotel)
    assert m["disclosure"]["your_level"] == "L1" and set(m["how_to_read"]["sensitivity_levels"]) == {"L0", "L1", "L2", "L3"}
    r = json.dumps(api.find_options(hotel, "200 litres of lactose-free milk tomorrow"))
    for withheld in ("stock_quantity", "delivery_reliability", "margin", "cost_cents"):
        assert withheld not in r


def test_orders_carry_the_checked_valio_signature(hotel):
    _, o = _place(hotel, "200 litres of lactose-free milk tomorrow")
    v = o["verification"]
    assert v["signed_by"] == "Valio" and v["signature_valid"] is True and v["expired"] is False
    assert v["protocol_id"] == "prot_hotel_v1"
