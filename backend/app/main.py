"""The Pass · Valio Aimo open procurement channel

BE1 routes (protocols, quotes, orders, approvals, audit) and BE3 routes (profiles, requests, select) are real.
BE2 routes still return fixtures; until BE2 lands, BE3 runs on its own stubs for the catalogue, price and stock.
Run: uvicorn app.main:app --reload    Docs: http://localhost:8000/docs
Tags name the owner: BE1 channel & transactions, BE2 catalogue & data, BE3 orchestration & AI.
"""
import functools
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal, Optional

import pydantic
from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from . import db, errors, fixtures as fx
from .channel import approvals, audit as audit_store, checkpoints, identity, orders, protocols, quotes, reports, seed, signing
from .internal import Audience
from .orchestrator import parser_api, seed as be3_seed, service as be3
from .orchestrator.holds import HoldCaller, record_parse
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
    title="The Pass · Valio Aimo open procurement channel",
    version="0.1.0",
    description="Open, verifiable gateway for AI purchasing agents.",
    responses={400: {"model": ErrorBody}, 403: {"model": ErrorBody}, 404: {"model": ErrorBody},
               409: {"model": ErrorBody}, 429: {"model": ErrorBody}, 502: {"model": ErrorBody}},
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

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

# ───── Profile（BE3）─────

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

# ───── 目录（BE2）─────

@app.get("/v1/catalog/facets", response_model=Facets, tags=["BE2 · catalog"])
def facets():
    """勾选面板的选项。"""
    return fx.FACETS

@app.post("/v1/products/search", response_model=list[ProductPublic], tags=["BE2 · catalog"])
def search_products(body: ProductSearchRequest, x_agent_key: Optional[str] = AgentKey):
    """只返回 L0 字段，不含价格和库存。"""
    return [fx.PRODUCT]

@app.get("/v1/products/{product_id}", response_model=ProductPublic, tags=["BE2 · catalog"])
def get_product(product_id: str):
    return fx.PRODUCT

# ───── 需求、候选、处理链（BE3）─────

@app.post("/v1/requests/parse", response_model=ParseResult, tags=["BE3 · requests"])
def parse_request(body: ParseRequest, who: Audience = Caller):
    """自然语言 → 预填面板。没有副作用，不建 request。"""
    # LLM via OpenRouter, rule-based without OPENROUTER_API_KEY. Creates no request; only writes hold events.
    outcome = parser_api.parse_detailed(body.text, be3.profile_for(body.profile_id, who))
    record_parse(audit_store, outcome, HoldCaller(buyer_id=who.buyer_id, agent_id=who.agent_id, actor=identity.actor(who)))
    return ParseResult(structured=outcome.structured, ambiguities=outcome.ambiguities,
                       profile_applied=outcome.profile_applied)

@app.post("/v1/requests", response_model=RequestResult, tags=["BE3 · requests"])
def create_request(body: CreateRequest, who: Audience = Caller):
    """提交确认后的需求，跑完四个节点，返回最多 8 个候选 + 标红列表 + 处理链。"""
    return be3.create_request(body, who)

@app.get("/v1/requests/{request_id}", response_model=RequestResult, tags=["BE3 · requests"])
def get_request(request_id: str, who: Audience = Caller):
    return be3.get_request(request_id, who)

@app.post("/v1/requests/{request_id}/nodes/{node}/reject", response_model=RequestResult, tags=["BE3 · requests"])
def reject_node(request_id: str, node: int, body: RejectNodeRequest, who: Audience = Caller):
    """否定第 node 个节点：该节点及之后重新生成，attempt + 1。每个节点最多 3 次（否则 RETRY_LIMIT）。
    第 4 个节点只接受 SEND_TO_APPROVAL 或 PICK_OTHER。"""
    return be3.reject_node(request_id, node, body, who)

@app.get("/v1/requests/{request_id}/chain", response_model=ChainTimeline, tags=["BE3 · requests"])
def get_chain(request_id: str, attempt: Optional[int] = None, who: Audience = Caller):
    return be3.chain(request_id, who, attempt)

@app.post("/v1/requests/{request_id}/select", response_model=SelectResult, tags=["BE3 · requests"])
def select_candidate(request_id: str, body: SelectRequest, who: Audience = Caller):
    """选中一个候选：调用 BE1 quotes.create + gate.evaluate，返回签名报价和判断结果。不能选标红的。"""
    return be3.select(request_id, body, who)

# ───── 报价、订单（BE1）─────

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

# ───── 管理（BE2 / BE1）─────

PROXY_HEADERS = ("cf-connecting-ip", "x-forwarded-for", "forwarded")
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _local_only(request: Request) -> None:
    """Admin routes (reset, faults) answer only on the machine running the backend. A request that came through a
    tunnel or proxy (e.g. cloudflared, which adds Cf-Connecting-Ip) is refused, so a public demo URL cannot wipe the
    data or bring back the public demo agent keys."""
    client = request.client.host if request.client else None
    if client not in LOCAL_HOSTS or any(h in request.headers for h in PROXY_HEADERS):
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
    return {"ok": True}
