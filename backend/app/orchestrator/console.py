"""Agent console: an external AI agent you can watch. The left side is the chat; the right side is everything PasStar
did behind each answer, which an assistant like ChatGPT never shows.

The agent is honest about being external: its only way into PasStar is the MCP endpoint over HTTP with an agent key,
the same JSON-RPC calls ChatGPT's plugin makes. It runs here only so the page can show each step:
  llm     a model on OpenRouter reads the PasStar skill and the server's instructions and decides which tools to call
  script  no model: a fixed purchasing flow on the same tools, for demos without network or key
  attacks the agent deliberately breaks the rules, one way after another, and each refusal is shown

Every step is streamed to the page as an event (see `Event`), with a "behind the scenes" part read from PasStar's own
records (the request's four steps, the audit entries the call produced). Only people on the PasStar machine can
start the console, so nobody can spend the model budget through a public tunnel.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

import httpx

from .. import db
from ..channel import identity
from ..channel.checkpoints import BLOCK_CODES
from ..errors import ApiError
from . import service
from .llm import PRESETS, URL

SKILL = Path(__file__).resolve().parents[3] / "plugin" / "passtar" / "skills" / "valio-purchasing" / "SKILL.md"
MAX_TOOL_ROUNDS = 8
Event = dict[str, Any]


# ───── the agent's only way in: MCP over HTTP with its key ─────

class McpConnection:
    """JSON-RPC to PasStar's /mcp, exactly as an external assistant sends it."""

    def __init__(self, key: str, base_url: Optional[str] = None, http: Optional[httpx.AsyncClient] = None) -> None:
        self.url = (base_url or os.environ.get("PASSTAR_SELF_URL", "http://127.0.0.1:8000")).rstrip("/") + "/mcp"
        self.http = http or httpx.AsyncClient(timeout=120)
        self.headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
                        **({"X-Agent-Key": key} if key else {})}
        self._id = 0

    async def _rpc(self, method: str, params: dict) -> dict:
        self._id += 1
        r = await self.http.post(self.url, headers=self.headers,
                                 json={"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            raise RuntimeError(body["error"].get("message", "MCP error"))
        return body["result"]

    async def initialize(self) -> dict:
        return await self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                              "clientInfo": {"name": "passtar-console-agent", "version": "0.1"}})

    async def tools(self) -> list[dict]:
        return (await self._rpc("tools/list", {}))["tools"]

    async def call(self, name: str, args: dict) -> tuple[bool, Any]:
        """(ok, result). A refusal comes back as ok=False with PasStar's error object."""
        res = await self._rpc("tools/call", {"name": name, "arguments": args})
        text = res["content"][0]["text"] if res.get("content") else "{}"
        if res.get("isError"):
            text = text[text.find("{"):] if "{" in text else json.dumps({"error": "TOOL_ERROR", "message": text})
        try:
            return not res.get("isError"), json.loads(text)
        except ValueError:
            return not res.get("isError"), {"text": text}

    async def aclose(self) -> None:
        await self.http.aclose()


# ───── behind the scenes: what PasStar did, from its own records ─────

def _who(key: str):
    try:
        return identity.caller(key, None)
    except ApiError:
        return None


def last_seq() -> int:
    return db.one("SELECT COALESCE(MAX(seq), 0) AS n FROM audit_log")["n"]


def behind(key: str, tool: str, ok: bool, result: Any, since_seq: int) -> dict:
    """The part of the call the assistant never shows: identity, the four steps, the gate, the seal, the log."""
    who = _who(key)
    out: dict[str, Any] = {
        "identity": {"agent_id": who.agent_id, "buyer_id": who.buyer_id, "sensitivity": who.ceiling} if who
        else {"agent_id": None, "note": "Unknown key: refused at the door"},
    }
    if not ok:
        code = (result or {}).get("error")
        out["refused"] = {"code": code, "message": (result or {}).get("message"),
                          "reported_to_valio": code in BLOCK_CODES}
    elif isinstance(result, dict):
        if result.get("request_id") and who and tool in ("find_options", "adjust_options", "get_options"):
            try:
                chain = service.get_request(result["request_id"], who)["chain"]
                out["steps"] = [{"node": n["node"], "name": n["name"], "summary": n["summary"]} for n in chain]
            except ApiError:
                pass
            out["options"] = {"shown": len(result.get("options", [])), "left_out": len(result.get("blocked", []))}
        if "verification" in result:
            out["seal"] = result["verification"]
            out["gate"] = {"status": result.get("status"), "summary": result.get("summary"), "reasons": result.get("reasons")}
        if tool == "get_mandate":
            out["mandate"] = result.get("mandate")
    rows = db.all("SELECT seq, event, json_extract(payload_json, '$.summary') AS summary, hash FROM audit_log"
                  " WHERE seq > ? ORDER BY seq", (since_seq,))
    out["audit"] = [{"seq": r["seq"], "event": r["event"], "summary": r["summary"], "hash": r["hash"][:12]} for r in rows]
    return out


async def _tool(conn: McpConnection, key: str, name: str, args: dict, emit_id: str) -> tuple[Event, Event, bool, Any]:
    start, seq = time.perf_counter(), last_seq()
    call = {"type": "tool_call", "id": emit_id, "name": name, "args": args}
    try:
        ok, result = await conn.call(name, args)
    except (httpx.HTTPError, RuntimeError) as e:
        ok, result = False, {"error": "CONNECTION", "message": f"Could not reach PasStar: {e}"}
    done = {"type": "tool_result", "id": emit_id, "name": name, "ok": ok, "ms": round((time.perf_counter() - start) * 1000),
            "result": result, "behind": behind(key, name, ok, result, seq)}
    return call, done, ok, result


# ───── llm mode ─────

def _openai_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                              "parameters": t.get("inputSchema", {"type": "object", "properties": {}})}}
            for t in tools]


def _system(instructions: str) -> str:
    skill = SKILL.read_text(encoding="utf-8") if SKILL.exists() else ""
    skill = re.sub(r"^---.*?---\s*", "", skill, flags=re.S)
    return (f"{instructions}\n\n{skill}\n\nReply in the user's language. Keep answers short and scannable: "
            "for options, show the card lines as a compact list.")


async def run_llm(key: str, history: list[dict], text: str,
                  post: Optional[Callable[[dict], Awaitable[dict]]] = None,
                  conn: Optional[McpConnection] = None) -> AsyncIterator[Event]:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key and post is None:
        yield {"type": "error", "message": "No OPENROUTER_API_KEY: switch to script mode, or set the key and restart."}
        return
    conn = conn or McpConnection(key)
    try:
        init = await conn.initialize()
        tools = await conn.tools()
        if not history:
            yield {"type": "connected", "server": init.get("serverInfo", {}).get("name"), "tools": [t["name"] for t in tools]}
        messages = history or [{"role": "system", "content": _system(init.get("instructions", ""))}]
        messages = messages + [{"role": "user", "content": text}]
        preset = PRESETS["strong"]
        model = os.environ.get("THE_PASS_LLM_AGENT", preset.model)
        params = preset.params if model == preset.model else {}

        async def default_post(body: dict) -> dict:
            async with httpx.AsyncClient(timeout=preset.timeout_s * 2) as h:
                r = await h.post(URL, json=body, headers={"Authorization": f"Bearer {api_key}", "X-Title": "PasStar console"})
                r.raise_for_status()
                return r.json()

        ask = post or default_post
        for round_ in range(MAX_TOOL_ROUNDS):
            yield {"type": "thinking"}
            reply = (await ask({"model": model, "messages": messages, "tools": _openai_tools(tools), **params}))["choices"][0]["message"]
            calls = reply.get("tool_calls") or []
            messages.append({"role": "assistant", "content": reply.get("content") or "", **({"tool_calls": calls} if calls else {})})
            if not calls:
                yield {"type": "assistant", "text": reply.get("content") or ""}
                break
            for c in calls:
                try:
                    args = json.loads(c["function"].get("arguments") or "{}")
                except ValueError:
                    args = {}
                call, done, _, result = await _tool(conn, key, c["function"]["name"], args, c["id"])
                yield call
                yield done
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(result)[:20000]})
        else:
            yield {"type": "assistant", "text": "I stopped after too many steps; please rephrase."}
        yield {"type": "done", "history": messages}
    except httpx.HTTPError as e:
        yield {"type": "error", "message": f"The model or PasStar could not be reached: {e}"}
    finally:
        await conn.aclose()


# ───── script mode: the same tools, a fixed flow, no model ─────

async def run_script(key: str, state: dict, text: str, conn: Optional[McpConnection] = None) -> AsyncIterator[Event]:
    """state carries what a person would remember: the request, the options, an open question, an order."""
    conn = conn or McpConnection(key)
    n = [0]

    async def tool(name: str, args: dict):
        n[0] += 1
        call, done, ok, result = await _tool(conn, key, name, args, f"s{n[0]}")
        yield_ = [call, done]
        return yield_, ok, result

    try:
        if not state.get("mandate"):
            await conn.initialize()
            events, ok, m = await tool("get_mandate", {})
            for e in events:
                yield e
            if not ok:
                yield {"type": "assistant", "text": f"I can't act for anyone: {m.get('message')}"}
                yield {"type": "done", "state": state}
                return
            state["mandate"] = m.get("mandate")
            yield {"type": "assistant", "text": m.get("mandate", "")}

        low = text.lower()
        pick = re.search(r"(?:option|#|order|choose|pick)\s*(\d+)", low) or re.fullmatch(r"\s*(\d+)\s*", low)
        if "approve" in low:
            yield {"type": "assistant", "text": "I can't approve orders: that is a person's decision in PasStar. "
                                                "I can check the order's status for you."}
        elif ("status" in low or "approved" in low) and state.get("order_id"):
            events, ok, o = await tool("order_status", {"order_id": state["order_id"]})
            for e in events:
                yield e
            yield {"type": "assistant", "text": f"Order {o.get('order_id')}: {o.get('status')}. {o.get('next_step', '')}"}
        elif pick and state.get("options") and not state.get("question"):
            i = int(pick.group(1)) - 1
            opts = state["options"]
            if not 0 <= i < len(opts):
                yield {"type": "assistant", "text": f"Choose a number from 1 to {len(opts)}."}
            else:
                events, ok, o = await tool("place_order", {"request_id": state["request_id"], "option_id": opts[i]["option_id"]})
                for e in events:
                    yield e
                if ok:
                    state["order_id"] = o["order_id"]
                    yield {"type": "assistant", "text": f"{o['status']}: {o.get('next_step', '')} "
                                                        f"Valio's signature {'checks out' if o['verification']['signature_valid'] else 'does NOT check out'}."}
                else:
                    yield {"type": "assistant", "text": f"PasStar refused: {o.get('message')}"}
        else:
            answers = {state["question"]: text} if state.get("question") else None
            base = state.get("text") if state.get("question") else text
            events, ok, r = await tool("find_options", {"text": base, **({"answers": answers} if answers else {})})
            for e in events:
                yield e
            if not ok:
                yield {"type": "assistant", "text": f"PasStar refused: {r.get('message')}"}
            elif r["status"] == "ONE_PRODUCT_AT_A_TIME":
                heard = ", ".join(p["text"] for p in r["products"])
                yield {"type": "assistant", "text": f"I heard several products: {heard}. {r['message']} "
                                                    "Which one shall we start with? Send me just that one."}
            elif r["status"] == "NEEDS_INPUT":
                q = r["questions"][0]
                state.update(text=base, question=q["field"])
                yield {"type": "assistant", "text": f"{r['understood_line']} {q['question']}"}
            elif r["status"] == "NO_OPTIONS":
                state.pop("question", None)
                extra = f" The first date that works is {r['earliest_delivery']['weekday']} {r['earliest_delivery']['date']}." if r.get("earliest_delivery") else ""
                yield {"type": "assistant", "text": f"{r['understood_line']} Nothing matched: {r.get('why_none')}.{extra}"}
            else:
                state.pop("question", None)
                state.update(request_id=r["request_id"], options=r["options"])
                cards = "\n\n".join("\n".join(o["card"]) for o in r["options"][:3])
                yield {"type": "assistant", "text": f"{r['understood_line']}\n\n{cards}\n\nWhich one shall I order? Say e.g. “option 1”."}
        yield {"type": "done", "state": state}
    finally:
        await conn.aclose()


# ───── attacks: the agent tries to break the rules ─────

ATTACKS = [
    ("no_key", "Call PasStar with a made-up key"),
    ("self_approve", "Approve its own held order"),
    ("over_maximum", "Order above the protocol's maximum"),
    ("other_buyer", "Read another buyer's request"),
]


# The attempts reuse the demo's quantities (1000 L held for approval, 4000 L over the maximum), so watching them does
# not use up Valio's probe budget of three different quantities per product a day.
async def run_attacks(key: str, conn_factory: Optional[Callable[[str], McpConnection]] = None,
                      rest: Optional[httpx.AsyncClient] = None) -> AsyncIterator[Event]:
    """Each attempt is a real call; PasStar's refusal (and whether Valio is told) is what the page shows."""
    make = conn_factory or (lambda k: McpConnection(k))
    base = os.environ.get("PASSTAR_SELF_URL", "http://127.0.0.1:8000").rstrip("/")
    http = rest or httpx.AsyncClient(timeout=120)
    who = _who(key)
    try:
        for attack, title in ATTACKS:
            yield {"type": "attack", "attack": attack, "title": title}
            if attack == "no_key":
                conn = make("tp_made_up_key_0000000000")
                try:
                    for e in (await _tool(conn, "tp_made_up_key_0000000000", "get_mandate", {}, attack))[:2]:
                        yield e
                finally:
                    await conn.aclose()
            elif attack == "self_approve":
                conn = make(key)
                try:
                    r = (await _tool(conn, key, "find_options", {"text": "1000 litres of lactose-free milk tomorrow"}, f"{attack}-1"))[3]
                    held = None
                    for o in (r or {}).get("options", []):
                        if o["if_chosen"]["outcome"] == "BUYER_APPROVAL":
                            held = (await _tool(conn, key, "place_order", {"request_id": r["request_id"], "option_id": o["option_id"]}, f"{attack}-2"))[3]
                            break
                    approval = (db.one("SELECT id FROM approvals WHERE order_id = ?", (held["order_id"],)) or {"id": None})["id"] if held and held.get("order_id") else None
                    seq, start = last_seq(), time.perf_counter()
                    path = f"/v1/approvals/{approval or 'apr_unknown'}/decision"
                    args = {"decision": "APPROVE"}
                    yield {"type": "tool_call", "id": attack, "name": f"POST {path}", "args": args, "rest": True}
                    resp = await http.post(base + path, json=args, headers={"X-Agent-Key": key})
                    body = resp.json()
                    ok = resp.status_code < 400
                    result = body if ok else body.get("error", body)
                    if not ok:
                        result = {"error": result.get("code"), "message": result.get("message")}
                    yield {"type": "tool_result", "id": attack, "name": f"POST {path}", "ok": ok,
                           "ms": round((time.perf_counter() - start) * 1000), "result": result,
                           "behind": behind(key, "approve", ok, result, seq)}
                finally:
                    await conn.aclose()
            elif attack == "over_maximum":
                conn = make(key)
                try:
                    r = (await _tool(conn, key, "find_options", {"text": "4000 litres of lactose-free milk tomorrow"}, f"{attack}-1"))[3]
                    opts = (r or {}).get("options") or []
                    if opts:
                        for e in (await _tool(conn, key, "place_order", {"request_id": r["request_id"], "option_id": opts[0]["option_id"]}, attack))[:2]:
                            yield e
                finally:
                    await conn.aclose()
            elif attack == "other_buyer":
                other = db.one("SELECT id FROM requests WHERE buyer_id != ? ORDER BY created_at DESC LIMIT 1",
                               (who.buyer_id if who else "",))
                conn = make(key)
                try:
                    for e in (await _tool(conn, key, "get_options", {"request_id": other["id"] if other else "req_someone_else"}, attack))[:2]:
                        yield e
                finally:
                    await conn.aclose()
        yield {"type": "done"}
    finally:
        if rest is None:
            await http.aclose()
