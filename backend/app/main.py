"""PasStar · Valio Aimo open procurement channel

BE1 routes (protocols, quotes, orders, approvals, audit) and BE3 routes (profiles, requests, select) are real.
BE2 routes still return fixtures; until BE2 lands, BE3 runs on its own stubs for the catalogue, price and stock.
Run: uvicorn app.main:app --reload    Docs: http://localhost:8000/docs
Tags name the owner: BE1 channel & transactions, BE2 catalogue & data, BE3 orchestration & AI.
"""
import functools
from pathlib import Path
import json
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal, Optional

import pydantic
from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from . import db, demo_access, errors, fixtures as fx
from .channel import agents, approvals, audit as audit_store, checkpoints, identity, orders, protocols, quotes, reports, seed, signing
from .internal import Audience
from .orchestrator import console, parser_api, seed as be3_seed, service as be3
from .orchestrator.holds import HoldCaller, record_parse
from .orchestrator.several import several_products
from .schemas import *  # noqa: F401,F403

try:        # MCP for AI assistants (BE3, orchestrator/mcp_server.py); the API runs without it when mcp is not installed
    from .orchestrator import mcp_server
except ImportError:
    mcp_server = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    if db.one("SELECT 1 FROM buyers") is None:
        seed.reset()
        be3_seed.reset()
    if mcp_server is None:
        yield
        return
    async with mcp_server.session_manager.run():
        yield


app = FastAPI(
    title="PasStar · Valio Aimo open procurement channel",
    version="0.1.0",
    description="Open, verifiable gateway for AI purchasing agents.",
    responses={400: {"model": ErrorBody}, 403: {"model": ErrorBody}, 404: {"model": ErrorBody},
               409: {"model": ErrorBody}, 429: {"model": ErrorBody}, 502: {"model": ErrorBody}},
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Requests through a tunnel or proxy carry one of these (cloudflared adds Cf-Connecting-Ip).
PROXY_HEADERS = ("cf-connecting-ip", "x-forwarded-for", "forwarded")
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _is_local(request: Request) -> bool:
    client = request.client.host if request.client else None
    return client in LOCAL_HOSTS and not any(h in request.headers for h in PROXY_HEADERS)


def _person_allowed(request: Request) -> bool:
    """Locally: requests from this machine. Public deployment: requests with a valid demo session (see demo_access)."""
    if demo_access.public():
        return demo_access.valid(request.headers.get("x-demo-session"))
    return _is_local(request)


@app.middleware("http")
async def _people_only_locally(request: Request, call_next):
    """People are identified by the X-Demo-User header, with no login (demo only). Through a public tunnel anyone
    could send it and act as the manager, so it is accepted only on the machine running PasStar, or on a public
    deployment with the demo passcode's session. Agents reach PasStar from anywhere with their X-Agent-Key."""
    if "x-demo-user" in request.headers and not _person_allowed(request):
        return errors.response("OUT_OF_SCOPE", "Sign in with the demo passcode first; agents use X-Agent-Key"
                               if demo_access.public() else
                               "Signing in as a person only works on the machine running PasStar; agents use X-Agent-Key", {})
    return await call_next(request)


class SessionRequest(pydantic.BaseModel):
    passcode: str


@app.get("/v1/session", tags=["BE1 · admin"])
def session_status(request: Request):
    """Whether this deployment asks for the demo passcode, and whether the caller's session is valid."""
    return {"public": demo_access.public(), "signed_in": _person_allowed(request)}


@app.post("/v1/session", tags=["BE1 · admin"])
def session_sign_in(body: SessionRequest):
    """Exchange the demo passcode for a session token (send it as X-Demo-Session)."""
    token = demo_access.sign_in(body.passcode)
    if token is None:
        raise errors.ApiError("NOT_APPROVER", "Wrong passcode")
    return {"token": token}

if mcp_server is not None:
    app.router.routes.extend(mcp_server.routes)      # POST /mcp

AgentKey = Header(default=None, alias="X-Agent-Key")
DemoUser = Header(default=None, alias="X-Demo-User",
                  description="usr_chef | usr_manager (SEPARATE mode) · usr_owner (SELF mode) · usr_valio")


# ───── Unified errors, identity (BE1) ─────

@app.exception_handler(errors.ApiError)
def _api_error(request: Request, e: errors.ApiError):
    agent_key = request.headers.get("X-Agent-Key")
    if agent_key and e.code in checkpoints.BLOCK_CODES:          # the channel refused an agent: goes into the hold report
        checkpoints.record_block(agent_key, f"{request.method} {request.scope['route'].path}", e.code, e.message)
    return errors.response(e.code, e.message, e.details)

@app.exception_handler(RequestValidationError)
@app.exception_handler(pydantic.ValidationError)
def _bad_request(_, e):
    problems = [{"loc": list(x["loc"]), "msg": x["msg"], "type": x["type"]} for x in e.errors()]
    return errors.response("VALIDATION_ERROR", "Request does not match the contract", {"errors": problems})


def _caller(x_agent_key: Optional[str] = AgentKey, x_demo_user: Optional[str] = DemoUser) -> Audience:
    return identity.caller(x_agent_key, x_demo_user)

Caller = Depends(_caller)


def _atomic(fn):
    """A BE1 write route runs as one transaction: when it fails half-way, nothing it wrote is kept. The refusal
    itself is logged afterwards by the error handler (record_block), outside the rolled-back transaction."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with db.transaction():
            return fn(*args, **kwargs)
    return wrapper


def _out(who: Audience, model) -> dict:
    """Every response passes the outbound check (disclosure.filter from BE2) before it is returned. No exceptions."""
    return checkpoints.check_response(who, model.model_dump(mode="json"))

# ───── Protocols (BE1) ─────

@app.get("/v1/protocols", response_model=list[Protocol], tags=["BE1 · protocols"])
def list_protocols(status: Optional[ProtocolStatus] = None, who: Audience = Caller):
    """Valio sees every buyer's protocols, a buyer its own. status=PENDING_VALIO_CONFIRMATION is Valio's to-do list."""
    if who.kind == "AGENT":
        raise errors.ApiError("OUT_OF_SCOPE", "Agents read their own protocol at /v1/agents/{agent_id}/protocol")
    return [_out(who, p) for p in protocols.list_for(who, status)]

@app.get("/v1/agents", response_model=list[AgentConnection], tags=["BE1 · agents"])
def list_agents(who: Audience = Caller):
    """External agents: kind, how they connect, key status, protocol and activity. Valio: all; a buyer: its own."""
    return [_out(who, a) for a in agents.list_for(who)]

@app.get("/v1/agents/{agent_id}", response_model=AgentConnection, tags=["BE1 · agents"])
def get_agent(agent_id: str, who: Audience = Caller):
    return _out(who, agents.get(agent_id, who))

@app.post("/v1/agents/{agent_id}/key", response_model=IssuedKey, tags=["BE1 · agents"])
@_atomic
def issue_agent_key(agent_id: str, body: IssueKeyRequest, who: Audience = Caller):
    """Issue a new key (shown once; PasStar keeps only its hash). The previous key stops working. Only the approver of
    the agent's active protocol may do this, so no agent gets a key before step 0."""
    return agents.issue_key(agent_id, body.kind, who)

@app.post("/v1/agents/{agent_id}/block", response_model=AgentConnection, tags=["BE1 · agents"])
@_atomic
def block_agent(agent_id: str, who: Audience = Caller):
    """Valio blocks an agent on its channel: every call is refused, even with a valid key. Nothing is deleted."""
    return _out(who, agents.set_blocked(agent_id, True, who))

@app.post("/v1/agents/{agent_id}/unblock", response_model=AgentConnection, tags=["BE1 · agents"])
@_atomic
def unblock_agent(agent_id: str, who: Audience = Caller):
    return _out(who, agents.set_blocked(agent_id, False, who))

@app.post("/v1/agents/{agent_id}/revoke", response_model=AgentConnection, tags=["BE1 · agents"])
@_atomic
def revoke_agent_key(agent_id: str, who: Audience = Caller):
    """Revoke the agent's key: every call with it is refused from now on (and shows up in the hold report)."""
    return _out(who, agents.revoke_key(agent_id, who))

# ───── Agent console (BE3): watch an external agent work, and what PasStar did behind each answer ─────

class ConsoleTurn(pydantic.BaseModel):
    agent_key: str
    text: str
    mode: Literal["llm", "script"] = "llm"
    history: list[dict] = []      # llm mode: the conversation so far, as returned in the last "done" event
    state: dict = {}              # script mode: what the scripted agent remembers


class ConsoleAttacks(pydantic.BaseModel):
    agent_key: str


def _console_guard(who: Audience, agent_key: str) -> None:
    """The buyer's people only: it is their agent and their key (Valio holds no buyer keys), and the model budget
    cannot be spent through a tunnel. A buyer drives only its own agents."""
    if who.kind != "BUYER_USER":
        raise errors.ApiError("OUT_OF_SCOPE", "The console is for the buyer's people; agents call /mcp directly")
    try:
        agent = identity.caller(agent_key, None)
    except errors.ApiError:
        return                      # an unknown key is allowed: watching it be refused is part of the demo
    if who.kind == "BUYER_USER" and agent.buyer_id != who.buyer_id:
        raise errors.ApiError("OUT_OF_SCOPE", "That key belongs to another buyer's agent")


def _sse(events):
    async def stream():
        async for e in events:
            yield f"data: {json.dumps(e, default=str)}\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/v1/console/turn", tags=["BE3 · console"])
def console_turn(body: ConsoleTurn, who: Audience = Caller):
    """One message to the console agent; streams its tool calls, PasStar's answers and what happened behind them."""
    _console_guard(who, body.agent_key)
    if body.mode == "script":
        return _sse(console.run_script(body.agent_key, dict(body.state), body.text))
    return _sse(console.run_llm(body.agent_key, body.history, body.text))


@app.post("/v1/console/attacks", tags=["BE3 · console"])
def console_attacks(body: ConsoleAttacks, who: Audience = Caller):
    """The agent tries to break the rules in several ways; streams each attempt and PasStar's refusal."""
    _console_guard(who, body.agent_key)
    return _sse(console.run_attacks(body.agent_key))


@app.get("/v1/agents/{agent_id}/protocol", response_model=Protocol, tags=["BE1 · protocols"])
def active_protocol(agent_id: str, who: Audience = Caller):
    """The protocol this agent acts under now (its latest ACTIVE version). Use it instead of a fixed protocol id:
    raising a limit creates a new version and retires the old one."""
    return _out(who, protocols.active_for_agent(agent_id, who))

@app.post("/v1/protocols/draft", response_model=Protocol, tags=["BE1 · protocols"])
def draft_protocol(body: ProtocolDraftRequest, who: Audience = Caller):
    """The agent drafts a protocol from one sentence (calls parser.draft_protocol from BE3); returns a DRAFT plus open questions."""
    return _out(who, protocols.draft(body, who))

@app.get("/v1/protocols/{protocol_id}", response_model=Protocol, tags=["BE1 · protocols"])
def get_protocol(protocol_id: str, who: Audience = Caller):
    return _out(who, protocols.get_for(protocol_id, who))

@app.put("/v1/protocols/{protocol_id}", response_model=Protocol, tags=["BE1 · protocols"])
@_atomic
def update_protocol(protocol_id: str, body: ProtocolUpdateRequest, who: Audience = Caller):
    """Only a DRAFT protocol can be edited."""
    return _out(who, protocols.update(protocol_id, body.body, who))

@app.post("/v1/protocols/{protocol_id}/sign", response_model=Protocol, tags=["BE1 · protocols"])
@_atomic
def sign_protocol(protocol_id: str, who: Audience = Caller):
    """The approver named in the protocol signs: DRAFT / PENDING_BUYER_SIGNATURE → PENDING_VALIO_CONFIRMATION. Agents cannot sign."""
    return _out(who, protocols.buyer_sign(protocol_id, who))

@app.post("/v1/protocols/{protocol_id}/confirm", response_model=Protocol, tags=["BE1 · protocols"])
@_atomic
def confirm_protocol(protocol_id: str, who: Audience = Caller):
    """Valio confirms: → ACTIVE with a jws; the previous version → SUPERSEDED."""
    return _out(who, protocols.confirm(protocol_id, who))

@app.post("/v1/protocols/{protocol_id}/amend", response_model=ProtocolDiff, tags=["BE1 · protocols"])
@_atomic
def amend_protocol(protocol_id: str, body: ProtocolAmendRequest, who: Audience = Caller):
    """Tightening takes effect at once; expansion creates a new version that must be signed again; changes the caller may not make are listed in forbidden and refused."""
    return _out(who, protocols.amend(protocol_id, body.changes, who))

@app.post("/v1/protocols/{protocol_id}/revoke", response_model=Protocol, tags=["BE1 · protocols"])
@_atomic
def revoke_protocol(protocol_id: str, who: Audience = Caller):
    """The approver named in the protocol or Valio can revoke it at any time."""
    return _out(who, protocols.revoke(protocol_id, who))

@app.get("/v1/.well-known/jwks.json", response_model=Jwks, tags=["BE1 · protocols"])
def jwks():
    """The public signing key of Valio; anyone can use it to verify protocols and quotes."""
    return signing.jwks()

# ───── Profile (BE3) ─────

@app.get("/v1/buyers/{buyer_id}/profiles", response_model=list[Profile], tags=["BE3 · profiles"])
def list_profiles(buyer_id: str, who: Audience = Caller):
    return be3.list_profiles(buyer_id, who)

@app.post("/v1/buyers/{buyer_id}/profiles", response_model=Profile, tags=["BE3 · profiles"])
def create_profile(buyer_id: str, body: ProfileBody, who: Audience = Caller):
    return be3.create_profile(buyer_id, body, who)

@app.get("/v1/profiles/{profile_id}", response_model=Profile, tags=["BE3 · profiles"])
def get_profile(profile_id: str, who: Audience = Caller):
    return checkpoints.check_response(who, be3.get_profile(profile_id, who).model_dump(mode="json"))

@app.put("/v1/profiles/{profile_id}", response_model=Profile, tags=["BE3 · profiles"])
def update_profile(profile_id: str, body: ProfileBody, who: Audience = Caller):
    return be3.update_profile(profile_id, body, who)

# ───── Catalogue (BE2) ─────

@app.get("/v1/catalog/facets", response_model=Facets, tags=["BE2 · catalog"])
def facets():
    """Options for the checkbox panel."""
    return fx.FACETS

@app.post("/v1/products/search", response_model=list[ProductPublic], tags=["BE2 · catalog"])
def search_products(body: ProductSearchRequest, x_agent_key: Optional[str] = AgentKey):
    """Returns L0 fields only, no prices or stock."""
    return [fx.PRODUCT]

@app.get("/v1/products/{product_id}", response_model=ProductPublic, tags=["BE2 · catalog"])
def get_product(product_id: str):
    return fx.PRODUCT

# ───── Requests, candidates, processing chain (BE3) ─────

@app.post("/v1/requests/parse", response_model=ParseResult, tags=["BE3 · requests"])
def parse_request(body: ParseRequest, who: Audience = Caller):
    """Natural language → prefilled panel. No side effects; does not create a request."""
    # LLM via OpenRouter, rule-based without OPENROUTER_API_KEY. Creates no request; only writes hold events.
    items = several_products(body.text)
    if items:   # one product at a time for now: no model call, the form shows the first product
        outcome = parser_api.rules_only(items[0]["text"], be3.profile_for(body.profile_id, who))
        return ParseResult(structured=outcome.structured, ambiguities=[], profile_applied=outcome.profile_applied,
                           several=[SeveralItem(**i) for i in items])
    outcome = parser_api.parse_detailed(body.text, be3.profile_for(body.profile_id, who))
    record_parse(audit_store, outcome, HoldCaller(buyer_id=who.buyer_id, agent_id=who.agent_id, actor=identity.actor(who)))
    return ParseResult(structured=outcome.structured, ambiguities=outcome.ambiguities,
                       profile_applied=outcome.profile_applied)

@app.post("/v1/requests", response_model=RequestResult, tags=["BE3 · requests"])
def create_request(body: CreateRequest, who: Audience = Caller):
    """Submit the confirmed request, run all four nodes, and return up to 8 candidates + the blocked list + the processing chain."""
    return be3.create_request(body, who)

@app.get("/v1/requests/{request_id}", response_model=RequestResult, tags=["BE3 · requests"])
def get_request(request_id: str, who: Audience = Caller):
    return be3.get_request(request_id, who)

@app.post("/v1/requests/{request_id}/nodes/{node}/reject", response_model=RequestResult, tags=["BE3 · requests"])
def reject_node(request_id: str, node: int, body: RejectNodeRequest, who: Audience = Caller):
    """Reject node number `node`: that node and everything after it is regenerated, attempt + 1. At most 3 attempts per node (otherwise RETRY_LIMIT).
    Node 4 only accepts SEND_TO_APPROVAL or PICK_OTHER."""
    return be3.reject_node(request_id, node, body, who)

@app.get("/v1/requests/{request_id}/chain", response_model=ChainTimeline, tags=["BE3 · requests"])
def get_chain(request_id: str, attempt: Optional[int] = None, who: Audience = Caller):
    return be3.chain(request_id, who, attempt)

@app.post("/v1/requests/{request_id}/select", response_model=SelectResult, tags=["BE3 · requests"])
def select_candidate(request_id: str, body: SelectRequest, who: Audience = Caller):
    """Select a candidate: calls BE1 quotes.create + gate.evaluate and returns the signed quote and the decision. Blocked candidates cannot be selected."""
    return be3.select(request_id, body, who)

# ───── Quotes, orders (BE1) ─────

@app.get("/v1/quotes/{quote_id}", response_model=Quote, tags=["BE1 · orders"])
def get_quote(quote_id: str, who: Audience = Caller):
    if who.kind != "VALIO_USER" and quotes.buyer_of(quote_id) != who.buyer_id:
        raise errors.ApiError("NOT_FOUND", f"Quote {quote_id} does not exist")
    return _out(who, quotes.get(quote_id))

@app.get("/v1/quotes/{quote_id}/verify", response_model=QuoteVerifyResult, tags=["BE1 · orders"])
def verify_quote(quote_id: str):
    """Checks the Valio signature and expiry. Anyone may verify."""
    return quotes.verify(quote_id)

@app.post("/v1/orders", response_model=Order, tags=["BE1 · orders"], responses={403: {"model": ErrorBody}})
@_atomic
def create_order(body: CreateOrderRequest, idempotency_key: str = Header(alias="Idempotency-Key"), who: Audience = Caller):
    """Recheck the quote (signature, expiry, price and stock) → gate → PLACED / PENDING_* /
    BUYER_AUTHORIZATION_REQUIRED / REQUOTE_REQUIRED / REJECTED. The same Idempotency-Key returns the same order.
    With an EXTERNAL-approval protocol and an over-limit order: no token → BUYER_AUTHORIZATION_REQUIRED (with
    authorization_request); a valid token → PLACED; an invalid token → 403 AUTHORIZATION_INVALID."""
    return _out(who, orders.submit(body.quote_id, body.protocol_id, idempotency_key, who, body.buyer_authorization))

@app.post("/v1/orders/{order_id}/authorization", response_model=Order, tags=["BE1 · orders"],
          responses={403: {"model": ErrorBody}})
@_atomic
def authorize_order(order_id: str, body: AuthorizeOrderRequest, who: Audience = Caller):
    """External approval: the agent returns with the token signed by the buyer system and the same order continues.
    The token is verified first (signature, quote, amount, approver, expiry), then the quote is rechecked;
    if the price moved → REQUOTE_REQUIRED, and the new quote needs a new token."""
    return _out(who, orders.authorize(order_id, body.buyer_authorization, who))

@app.get("/v1/orders", response_model=list[OrderSummary], tags=["BE1 · orders"])
def list_orders(who: Audience = Caller):
    """The buyer's orders, newest first, whoever placed them: a person or one of its agents. Valio: every buyer's."""
    return [_out(who, o) for o in orders.list_for(who)]

@app.get("/v1/orders/{order_id}", response_model=Order, tags=["BE1 · orders"])
def get_order(order_id: str, who: Audience = Caller):
    return _out(who, orders.get(order_id, who))

# ───── Approvals (BE1) ─────

@app.get("/v1/approvals", response_model=list[ApprovalCard], tags=["BE1 · approvals"])
def list_approvals(kind: Optional[Literal["BUYER", "VALIO"]] = None,
                   status: Optional[Literal["PENDING", "APPROVED", "APPROVED_WITH_CHANGES", "REJECTED", "ROUTED"]] = None,
                   who: Audience = Caller):
    """The buyer side sees only its own buyer approvals; Valio sees both queues."""
    return [_out(who, c) for c in approvals.list_cards(kind, status, who)]

@app.get("/v1/approvals/{approval_id}", response_model=ApprovalCard, tags=["BE1 · approvals"])
def get_approval(approval_id: str, who: Audience = Caller):
    return _out(who, approvals.get_card(approval_id, who))

@app.post("/v1/approvals/{approval_id}/decision", response_model=ApprovalDecisionResult, tags=["BE1 · approvals"],
          responses={403: {"model": ErrorBody}})
@_atomic
def decide(approval_id: str, body: ApprovalDecision, who: Audience = Caller):
    """Checks the approver, then rechecks the quote: unchanged → order placed; changed → REQUOTE_REQUIRED (with a new
    quote, which goes through the gate as usual when it is ordered).
    Approver check (BUYER approvals):
      - the decider must be the approver in the protocol, else 403 NOT_APPROVER;
      - in SEPARATE mode the decider cannot be the requester, else 403 SELF_APPROVAL_FORBIDDEN;
      - in SELF mode self-approval is allowed and logged as "self-approved".
    APPROVE_WITH_CHANGES: the approver changes proposal parameters only (`changes`) and must give a reason (`note`);
    the changed proposal goes back through the gateway and gets a newly signed quote.
      - Changeable: `candidate_id` (a substitute from the candidates), `delivery_date`, `lines[].sales_units`
        (down only); Valio may also change `lines[].unit_price_cents` (within the reviewer band, beyond it the
        approval is ROUTED), `conditions` and `valid_minutes`; data requests take `disclose_fields`, a per-field
        switch. Everything else is read-only.
      - A Valio change is a new quote round: the order becomes REQUOTE_REQUIRED and the buyer orders again with
        the new quote within the confirmation window."""
    return _out(who, approvals.decide(approval_id, body, who))

# ───── Audit (BE1) ─────

@app.get("/v1/audit", response_model=list[AuditEntry], tags=["BE1 · audit"])
def audit(request_id: Optional[str] = None, who: Audience = Caller):
    """Filtered by role: a buyer sees only its own entries with visibility BUYER / BOTH; Valio sees everything."""
    checkpoints.flush_blocks()
    return [checkpoints.check_response(who, e) for e in checkpoints.check_log(who, audit_store.entries(request_id))]

@app.get("/v1/audit/verify", response_model=AuditVerifyResult, tags=["BE1 · audit"])
def audit_verify():
    """Recomputes the whole hash chain and compares the buyer_id / agent_id / reason_code columns with their hashed
    copies in the payload; the first tampered entry is reported in first_bad_seq."""
    return audit_store.verify()

@app.get("/v1/reports/holds", response_model=reports.HoldsReport, tags=["BE1 · audit"],
         responses={403: {"model": ErrorBody}, 422: {"model": ErrorBody}})
def holds_report(since: Optional[datetime] = None, until: Optional[datetime] = None,
                 buyer_id: Optional[str] = None, agent_id: Optional[str] = None,
                 x_demo_user: Optional[str] = DemoUser, x_agent_key: Optional[str] = AgentKey):
    """Held / blocked events. Buyers see their own items; Valio sees counts only. Agents cannot read reports.
    since defaults to 7 days before until, until to now; a range above 90 days is refused.
    buyer_id is ignored in the buyer view (always the caller's buyer) and filters the Valio view."""
    if not x_demo_user:
        raise errors.ApiError("OUT_OF_SCOPE", "Hold reports are read by people: send X-Demo-User")
    who = identity.caller(None, x_demo_user)
    return _out(who, reports.holds(who, since, until, buyer_id, agent_id))

# ───── Admin (BE2 / BE1) ─────

def _local_only(request: Request) -> None:
    """Admin routes (reset, faults) answer only on the machine running the backend. A request that came through a
    tunnel or proxy (e.g. cloudflared, which adds Cf-Connecting-Ip) is refused, so a public demo URL cannot wipe the
    data or bring back the public demo agent keys. On a public deployment a signed-in demo session may use them."""
    if demo_access.public() and demo_access.valid(request.headers.get("x-demo-session")):
        return
    if not _is_local(request):
        raise errors.ApiError("OUT_OF_SCOPE", "Admin endpoints are only available on the machine running PasStar")

LocalOnly = Depends(_local_only)


@app.get("/v1/admin/faults", response_model=list[Fault], tags=["BE2 · admin"], dependencies=[LocalOnly])
def list_faults():
    return fx.FAULTS

@app.put("/v1/admin/faults/{name}", response_model=Fault, tags=["BE2 · admin"], dependencies=[LocalOnly])
def set_fault(name: Literal["PRICE_CHANGE", "PARTIAL_STOCK", "TIMEOUT", "RATE_LIMIT", "EMPTY_RESULT"], body: FaultUpdate):
    return Fault(name=name, enabled=body.enabled, params=body.params)

@app.post("/v1/admin/reset", tags=["BE1 · admin"], dependencies=[LocalOnly])
@_atomic
def reset():
    """Clears the BE1 tables (buyers, users, agents, protocols, quotes, orders, approvals, audit) and writes the demo seed data;
    then clears the BE3 tables and writes the demo profiles."""
    seed.reset()
    be3_seed.reset()
    # Valio probe budgets live in BE2's runtime database: a demo reset starts every agent from zero there too.
    # Only the budgets are cleared; the probe log stays.
    from .valio.client import reset_probe_budgets
    return {"ok": True, "probe_budgets_cleared": reset_probe_budgets()}


# ───── The built frontend (deployment) ─────
# When frontend/dist exists (the Docker image builds it), the same server serves the screens at "/", so the
# screens, the API and /mcp share one address. Registered last: every API route above takes precedence.
_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _DIST.is_dir():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="frontend")
