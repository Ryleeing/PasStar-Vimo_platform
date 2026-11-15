"""Agent console: script mode, the rule-breaking attempts, and what PasStar shows behind each call.

The wire format of /mcp is covered by test_mcp.py; here a stand-in connection calls the same tools in-process."""
import asyncio
import json

from fastapi.testclient import TestClient

from app.channel import identity
from app.errors import ApiError
from app.main import app
from app.orchestrator import agent_api, console

TOOLS = {"get_mandate": agent_api.get_mandate, "find_options": agent_api.find_options, "get_options": agent_api.get_options,
         "adjust_options": agent_api.adjust_options, "place_order": agent_api.place_order, "order_status": agent_api.order_status}


class InProcess(console.McpConnection):
    def __init__(self, key):
        self.key = key

    async def initialize(self):
        return {"serverInfo": {"name": "passtar"}, "instructions": "Start with get_mandate."}

    async def tools(self):
        return [{"name": n, "inputSchema": {"type": "object", "properties": {}}} for n in TOOLS]

    async def call(self, name, args):
        try:
            return True, TOOLS[name](identity.caller(self.key, None), **args)
        except ApiError as e:
            return False, {"error": e.code, "message": e.message}

    async def aclose(self):
        pass


def collect(gen):
    async def run():
        return [e async for e in gen]
    return asyncio.run(run())


def test_script_mode_asks_then_shows_options_with_whats_behind():
    state = {}
    first = collect(console.run_script("demo-hotel", state, "lactose-free milk tomorrow", conn=InProcess("demo-hotel")))
    assert [e["name"] for e in first if e["type"] == "tool_call"] == ["get_mandate", "find_options"]
    assert any(e["type"] == "assistant" and "Your mandate" in e["text"] for e in first)
    assert state["question"] == "quantity"

    second = collect(console.run_script("demo-hotel", state, "200 litres", conn=InProcess("demo-hotel")))
    result = next(e for e in second if e["type"] == "tool_result")
    assert result["ok"] and result["result"]["status"] == "OPTIONS"
    behind = result["behind"]
    assert behind["identity"]["agent_id"] == "agt_hotel" and behind["identity"]["sensitivity"] == "L1"
    assert [s["name"] for s in behind["steps"]] == ["UNDERSTAND", "FILTER", "RANK", "GATE"]
    assert behind["options"]["shown"] >= 1


def test_script_mode_orders_and_shows_the_seal():
    state = {}
    collect(console.run_script("demo-hotel", state, "200 litres of lactose-free milk tomorrow", conn=InProcess("demo-hotel")))
    events = collect(console.run_script("demo-hotel", state, "option 1", conn=InProcess("demo-hotel")))
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["name"] == "place_order" and result["behind"]["seal"]["signature_valid"] is True
    assert any(a["event"] == "ORDER_PLACED" or a["event"] == "ORDER_HELD" for a in result["behind"]["audit"])


def test_script_mode_never_approves():
    state = {}
    collect(console.run_script("demo-hotel", state, "200 litres of lactose-free milk tomorrow", conn=InProcess("demo-hotel")))
    events = collect(console.run_script("demo-hotel", state, "please approve it", conn=InProcess("demo-hotel")))
    assert not [e for e in events if e["type"] == "tool_call"]
    assert "can't approve" in next(e for e in events if e["type"] == "assistant")["text"]


def test_unknown_key_is_refused_at_the_door():
    events = collect(console.run_script("tp_made_up", {}, "milk", conn=InProcess("tp_made_up")))
    result = next(e for e in events if e["type"] == "tool_result")
    assert not result["ok"] and result["behind"]["refused"]["code"] == "PROTOCOL_INVALID"
    assert result["behind"]["refused"]["reported_to_valio"] is True


def test_attacks_are_each_refused():
    class Rest:
        def __init__(self):
            self.client = TestClient(app)

        async def post(self, url, json, headers):
            return self.client.post(url.split("8000", 1)[-1], json=json, headers=headers)

    events = collect(console.run_attacks("demo-hotel", conn_factory=InProcess, rest=Rest()))
    titles = [e["attack"] for e in events if e["type"] == "attack"]
    assert titles == [a for a, _ in console.ATTACKS]
    by_attack = {}
    current = None
    for e in events:
        if e["type"] == "attack":
            current = e["attack"]
        elif e["type"] == "tool_result":
            by_attack[current] = e
    assert by_attack["no_key"]["behind"]["refused"]["code"] == "PROTOCOL_INVALID"
    assert by_attack["self_approve"]["ok"] is False
    assert by_attack["over_maximum"]["result"]["status"] == "REJECTED"
    assert by_attack["other_buyer"]["ok"] is False


def test_llm_mode_runs_tools_the_model_asks_for():
    replies = iter([
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "get_mandate", "arguments": "{}"}}]}}]},
        {"choices": [{"message": {"content": "You buy for Hotel Helsinki Central."}}]},
    ])
    seen = []

    async def post(body):
        seen.append(body)
        return next(replies)

    events = collect(console.run_llm("demo-hotel", [], "who do you buy for?", post=post, conn=InProcess("demo-hotel")))
    kinds = [e["type"] for e in events]
    assert kinds[0] == "connected" and "tool_call" in kinds and kinds[-1] == "done"
    assert next(e for e in events if e["type"] == "assistant")["text"].startswith("You buy for")
    assert {t["function"]["name"] for t in seen[0]["tools"]} == set(TOOLS)
    history = next(e for e in events if e["type"] == "done")["history"]
    assert history[0]["role"] == "system" and any(m["role"] == "tool" for m in history)


def test_console_is_for_people_on_this_machine_and_their_own_agents():
    c = TestClient(app)
    body = {"agent_key": "demo-hotel", "text": "hi", "mode": "script"}
    assert c.post("/v1/console/turn", json=body, headers={"X-Agent-Key": "demo-hotel"}).status_code == 403
    assert c.post("/v1/console/turn", json=body, headers={"X-Demo-User": "usr_owner"}).json()["error"]["code"] == "OUT_OF_SCOPE"
    assert c.post("/v1/console/turn", json=body, headers={"X-Demo-User": "usr_valio"}).status_code == 403
    tunnel = {"X-Demo-User": "usr_manager", "Cf-Connecting-Ip": "203.0.113.7"}
    assert c.post("/v1/console/turn", json=body, headers=tunnel).status_code == 403
    assert json  # keep the import used


def test_script_mode_answers_several_products_kindly():
    events = collect(console.run_script("demo-hotel", {"mandate": "x"}, "200 l milk and 20 l cream tomorrow", conn=InProcess("demo-hotel")))
    said = next(e for e in events if e["type"] == "assistant")["text"]
    assert "next version" in said and "milk" in said and "cream" in said
