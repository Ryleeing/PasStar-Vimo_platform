"""The four checkpoints (CheckpointsApi). Every request, from an AI agent or a plain script, passes the same checks:
the guardrails are in the channel, not in the agent prompt.

A request from an agent that the channel refuses is written to the audit log as AGENT_BLOCKED (record_block, called
where errors become HTTP responses): at most one entry per agent and reason per minute, the rest are counted in
its "suppressed". The log is append-only, so an entry is written once its count is known: when the log is read
(flush_blocks) or when the next block arrives after the minute has passed."""
from __future__ import annotations

import time
from collections import defaultdict, deque

from .. import db, deps
from ..errors import ApiError
from ..internal import Audience
from ..schemas import Protocol, Quote
from . import audit, quotes, requests_view
from .identity import key_hash

MAX_PRODUCTS_PER_QUERY = 8                              # query candidates only, never crawl the catalogue
_calls: dict[str, deque] = defaultdict(deque)           # caller → call times within the last minute


BLOCK_CODES = {"PROTOCOL_INVALID", "OUT_OF_SCOPE", "RATE_LIMITED", "PROBE_LIMIT", "AUTHORIZATION_INVALID"}
BLOCK_WINDOW_S = 60
_pending: dict[tuple, dict] = {}                        # (agent_id, code) → blocks not yet in the log
_written: dict[tuple, float] = {}                       # (agent_id, code) → when its last entry was written


def record_block(agent_key: str, endpoint: str, code: str, message: str) -> None:
    """An agent request was refused with one of BLOCK_CODES. An unknown key is an unregistered agent (agent_id None)."""
    flush_blocks(min_age_s=BLOCK_WINDOW_S)
    a = db.one("SELECT id, buyer_id FROM agents WHERE api_key_hash = ?", (key_hash(agent_key),))
    block = _pending.setdefault((a and a["id"], code), {"buyer_id": a and a["buyer_id"], "endpoint": endpoint,
                                                        "message": message, "count": 0, "since": time.monotonic()})
    block["count"] += 1


def flush_blocks(min_age_s: float = 0) -> None:
    """Write the pending blocks whose agent and reason have had no entry within the last minute. Readers of the log
    flush everything that may be written; a new block flushes only what has been pending for min_age_s."""
    now = time.monotonic()
    for key in [k for k, b in _pending.items() if now - b["since"] >= min_age_s
                and (k not in _written or now - _written[k] >= BLOCK_WINDOW_S)]:
        (agent_id, code), block = key, _pending.pop(key)
        _written[key] = now
        audit.append("AGENT_BLOCKED", f"agent:{agent_id or 'unregistered'}",
                     {"buyer_id": block["buyer_id"], "agent_id": agent_id, "reason_code": code,
                      "endpoint": block["endpoint"], "suppressed": block["count"] - 1},
                     visibility="BOTH" if agent_id else "VALIO",       # a buyer sees the blocks of its own agents
                     summary=f"Blocked {agent_id or 'an unregistered agent'}: {block['message']}")



def check_query(audience: Audience, protocol: Protocol, product_ids: list[str]) -> None:
    """Before querying price and stock: the resource belongs to this buyer, the agent is the one named in the
    protocol, no bulk queries, within the rate limit."""
    if audience.buyer_id != protocol.body.buyer_id:
        raise ApiError("OUT_OF_SCOPE", "This protocol belongs to another buyer", protocol_id=protocol.id)
    if audience.kind == "AGENT" and audience.agent_id != protocol.body.agent_id:
        raise ApiError("PROTOCOL_INVALID", "This agent is not the agent named in the protocol", protocol_id=protocol.id)
    if len(product_ids) > MAX_PRODUCTS_PER_QUERY:
        raise ApiError("OUT_OF_SCOPE", f"At most {MAX_PRODUCTS_PER_QUERY} products per query", requested=len(product_ids))
    calls, now = _calls[audience.agent_id or audience.buyer_id], time.monotonic()
    while calls and now - calls[0] > 60:
        calls.popleft()
    limit = protocol.body.valio_scope.rate_limit_per_min
    if len(calls) >= limit:
        raise ApiError("RATE_LIMITED", f"More than {limit} queries per minute", limit_per_min=limit)
    calls.append(now)


def check_response(audience: Audience, payload: dict) -> dict:
    """Before responding: keep only the fields this caller may see (the BE2 outbound filter, no exceptions)."""
    return deps.disclosure.filter(payload, audience)


def check_submit(quote: Quote, protocol: Protocol) -> None:
    """Before placing an order: the quote belongs to this protocol, carries a valid signature and is still valid."""
    if quote.protocol_id != protocol.id:
        raise ApiError("OUT_OF_SCOPE", f"Quote {quote.id} was issued under another protocol")
    if not quotes.verify(quote.id).signature_valid:
        raise ApiError("OUT_OF_SCOPE", f"Quote {quote.id} does not carry a valid Valio signature")
    if quote.status == "EXPIRED":
        raise ApiError("QUOTE_EXPIRED", f"Quote {quote.id} expired at {quote.valid_until.isoformat()}; select again")
    if quote.status == "SUPERSEDED":
        raise ApiError("QUOTE_CHANGED", f"Quote {quote.id} was replaced by a newer quote")
    if quote.status == "USED":
        raise ApiError("QUOTE_EXPIRED", f"Quote {quote.id} has already been ordered; select again for a new quote")


def check_log(audience: Audience, entries: list[dict]) -> list[dict]:
    """Before returning the log: the buyer side sees only its own buyer-visible entries; Valio sees everything.
    An entry belongs to the buyer named in its payload, or else to the buyer of its request."""
    if audience.kind == "VALIO_USER":
        return entries

    def buyer(e: dict):
        return e["payload"].get("buyer_id") or (e["request_id"] and requests_view.buyer_of(e["request_id"]))

    return [e for e in entries if e["visibility"] in ("BUYER", "BOTH") and buyer(e) == audience.buyer_id]
