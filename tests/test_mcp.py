"""The MCP endpoint over real HTTP JSON-RPC, as ChatGPT or Claude call it: tools listed, key required, results JSON."""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

pytest.importorskip("mcp")

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def client():
    # One client for the module: the MCP session manager can start only once per process.
    with TestClient(app) as c:
        yield c


def rpc(client, method, params=None, key="demo-hotel", id_=1):
    headers = dict(HEADERS, **({"X-Agent-Key": key} if key else {}))
    r = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}})
    assert r.status_code == 200, r.text
    return r.json()


def call(client, tool, key="demo-hotel", **arguments):
    res = rpc(client, "tools/call", {"name": tool, "arguments": arguments}, key=key)["result"]
    text = res["content"][0]["text"]
    if res.get("isError"):
        text = text[text.index("{"):]            # "Error executing tool <name>: {...}"
    return res.get("isError", False), json.loads(text)


def test_initialize_returns_the_instructions(client):
    res = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": "test", "version": "0"}})["result"]
    assert res["serverInfo"]["name"] == "the-pass" and "get_mandate" in res["instructions"]


def test_six_tools_and_no_approval(client):
    tools = {t["name"]: t for t in rpc(client, "tools/list")["result"]["tools"]}
    assert set(tools) == {"get_mandate", "find_options", "get_options", "adjust_options", "place_order", "order_status"}
    assert tools["get_mandate"]["annotations"]["readOnlyHint"] is True
    assert tools["place_order"]["annotations"]["readOnlyHint"] is False
    assert "text" in tools["find_options"]["inputSchema"]["properties"]


def test_mandate_over_mcp(client):
    err, m = call(client, "get_mandate")
    assert not err and m["buyer"]["id"] == "buy_hotel"


def test_missing_key_is_a_readable_tool_error(client):
    err, body = call(client, "get_mandate", key=None)
    assert err and body["error"] == "PROTOCOL_INVALID"


def test_unknown_key_is_refused(client):
    err, body = call(client, "get_mandate", key="not-a-key")
    assert err and body["error"] == "PROTOCOL_INVALID"


def test_full_flow_question_options_order(client):
    err, r = call(client, "find_options", text="lactose-free milk tomorrow")
    assert not err and r["status"] == "NEEDS_INPUT"
    err, r = call(client, "find_options", text="lactose-free milk tomorrow", answers={"quantity": "200 litres"})
    assert not err and r["status"] == "OPTIONS"
    err, o = call(client, "place_order", request_id=r["request_id"], option_id=r["options"][0]["option_id"])
    assert not err and o["verification"]["signature_valid"] is True
    err, s = call(client, "order_status", order_id=o["order_id"])
    assert not err and s["status"] == o["status"]


def test_agent_without_protocol_is_refused(client):
    err, body = call(client, "find_options", key="demo-rogue", text="200 litres of lactose-free milk tomorrow")
    assert err and body["error"] == "PROTOCOL_INVALID" and "no active protocol" in body["message"]
