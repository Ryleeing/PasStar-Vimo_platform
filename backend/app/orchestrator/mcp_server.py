"""MCP server: The Pass as tools for an AI assistant (ChatGPT or Claude plugin), at POST /mcp.

The assistant connects with the buyer's agent key in the X-Agent-Key header (an API-key auth with a custom header
in the plugin's mcp.json), exactly like the HTTP API. Each tool is a thin wrapper around agent_api; refusals come
back as tool errors the model can read, and refusals of the kinds BE1 reports (PROTOCOL_INVALID, OUT_OF_SCOPE, ...)
go into the hold report as they do for the HTTP API.

Stateless HTTP with JSON responses: every call carries the key, so no MCP session is kept.
DNS-rebinding protection is off because the server is reached through a tunnel under changing host names and
every tool call is authenticated by the agent key anyway.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from ..channel import checkpoints, identity
from ..errors import ApiError
from . import agent_api

INSTRUCTIONS = """\
The Pass is Valio Aimo's ordering channel. You act as the purchasing agent of one buyer (a hotel, hospital or
restaurant kitchen), within the protocol that buyer signed: what you may buy, how much you may spend on your own,
and who must approve the rest.

Start every conversation with get_mandate. It tells you who you act for, your limits, how to read trust grades,
reliability, sensitivity levels and data sources, and the rules you must follow.

Typical flow: find_options with the user's own words -> if NEEDS_INPUT, ask the user and call find_options again
with their replies in answers -> present the options -> adjust_options if the user wants changes -> place_order
with the option the user picked -> order_status later for held orders.

You can never approve an order. Never say an order is placed unless its status is PLACED. Never present a
grade C or D requirement, or a fact from PRODUCT_NAME, NAME_PARSE, NO_DATA or DEMO_DATA, as verified.
"""

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

server = MCPServer("the-pass", title="The Pass · Valio Aimo", instructions=INSTRUCTIONS, version="0.1.0")


def _call(ctx: Context, tool: str, fn: Callable[..., dict], *args: Any, **kwargs: Any) -> dict:
    """Resolve the caller from X-Agent-Key, run the operation, and turn refusals into tool errors."""
    key = (ctx.headers or {}).get("x-agent-key")
    try:
        if not key:
            raise ApiError("PROTOCOL_INVALID", "Missing X-Agent-Key: connect with the buyer's agent key")
        return fn(identity.caller(key, None), *args, **kwargs)
    except ApiError as e:
        if key and e.code in checkpoints.BLOCK_CODES:
            checkpoints.record_block(key, f"MCP {tool}", e.code, e.message)
        raise ToolError(json.dumps({"error": e.code, "message": e.message, "details": e.details})) from e


@server.tool(annotations=READ)
def get_mandate(ctx: Context) -> dict:
    """Who you act for, what you may buy, your spending limits and approver, how to read results, and your rules.
    Call this first."""
    return _call(ctx, "get_mandate", agent_api.get_mandate)


@server.tool(annotations=READ)
def find_options(ctx: Context, text: str, answers: Optional[dict[str, str]] = None,
                 profile_id: Optional[str] = None) -> dict:
    """Turn the user's request into ranked options from Valio's catalogue, priced for this buyer.

    text: the user's request in their own words, e.g. "1000 litres of lactose-free milk for Monday".
    answers: the user's replies to questions from an earlier NEEDS_INPUT result, keyed by the question's field,
      e.g. {"quantity": "200 litres"}. Send the same text again with them.
    profile_id: optional kitchen profile from get_mandate (brand exclusions, ranking weights).

    Returns NEEDS_INPUT with questions, NO_OPTIONS with the reason, or OPTIONS (up to 8, ranked, each with price,
    delivery, reliability, requirement grades, sources, tips and what choosing it will do) plus blocked products."""
    return _call(ctx, "find_options", agent_api.find_options, text, answers, profile_id)


@server.tool(annotations=READ)
def get_options(ctx: Context, request_id: str) -> dict:
    """The current options of an earlier request."""
    return _call(ctx, "get_options", agent_api.get_options, request_id)


@server.tool(annotations=WRITE)
def adjust_options(ctx: Context, request_id: str, change: str, value: Optional[str] = None) -> dict:
    """Change one thing about a request's options and get the new list. Never loosens the protocol.

    change, and the value it needs:
      allow_brand (brand)         include a brand the kitchen profile excludes, for this request only
      exclude_product (product_id)
      prioritise (price | reliability | carbon | local)
      exclude_option (option_id)
      single_product              prefer options with one product
      require_approval            hold whatever is chosen for human approval
    To change quantity, date or product, call find_options again instead."""
    return _call(ctx, "adjust_options", agent_api.adjust_options, request_id, change, value)


@server.tool(annotations=WRITE)
def place_order(ctx: Context, request_id: str, option_id: str) -> dict:
    """Order the option the user chose. Only call this after the user picked it and you told them what choosing it
    will do (if_chosen) and the total.

    Valio signs a quote for it and the buyer's protocol decides: PLACED, PENDING_BUYER_APPROVAL (a person must
    approve), REQUOTE_REQUIRED or REJECTED. The result includes the checked Valio signature. Calling it again for
    the same option returns the same order."""
    return _call(ctx, "place_order", agent_api.place_order, request_id, option_id)


@server.tool(annotations=READ)
def order_status(ctx: Context, order_id: str) -> dict:
    """The current status of an order, e.g. whether a held order was approved, and what to tell the user."""
    return _call(ctx, "order_status", agent_api.order_status, order_id)


http_app = server.streamable_http_app(
    streamable_http_path="/mcp", stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
routes = http_app.routes
session_manager = server.session_manager
