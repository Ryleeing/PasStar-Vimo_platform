"""The Pass · M1 假数据服务器

每个路由先返回 fixtures 里的对象，等各自的实现完成后再替换。
运行：uvicorn app.main:app --reload    文档：http://localhost:8000/docs
tags 里写了负责人：BE1 通道与交易、BE2 目录与数据、BE3 编排与 AI。
"""
from typing import Literal, Optional

from fastapi import FastAPI, Header, Query
from fastapi.middleware.cors import CORSMiddleware

from . import fixtures as fx
from .schemas import *  # noqa: F401,F403

app = FastAPI(
    title="The Pass · Valio Aimo open procurement channel",
    version="0.1.0",
    description="Open, verifiable gateway for AI purchasing agents. Mock server (M1).",
    responses={400: {"model": ErrorBody}, 403: {"model": ErrorBody}, 404: {"model": ErrorBody},
               409: {"model": ErrorBody}, 429: {"model": ErrorBody}, 502: {"model": ErrorBody}},
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

AgentKey = Header(default=None, alias="X-Agent-Key")
DemoUser = Header(default=None, alias="X-Demo-User", description="chef | buyer_manager | valio_reviewer")

# ───── 协议（BE1）─────

@app.post("/v1/protocols/draft", response_model=Protocol, tags=["BE1 · protocols"])
def draft_protocol(body: ProtocolDraftRequest, x_demo_user: Optional[str] = DemoUser):
    """agent 根据一句话起草协议（内部调用 BE3 parser.draft_protocol），返回 DRAFT + 待确认问题。"""
    return fx.PROTOCOL_DRAFT

@app.get("/v1/protocols/{protocol_id}", response_model=Protocol, tags=["BE1 · protocols"])
def get_protocol(protocol_id: str):
    return fx.PROTOCOL

@app.put("/v1/protocols/{protocol_id}", response_model=Protocol, tags=["BE1 · protocols"])
def update_protocol(protocol_id: str, body: ProtocolUpdateRequest):
    """只能改 DRAFT 状态的协议。"""
    return fx.PROTOCOL_DRAFT

@app.post("/v1/protocols/{protocol_id}/sign", response_model=Protocol, tags=["BE1 · protocols"])
def sign_protocol(protocol_id: str, x_demo_user: Optional[str] = DemoUser):
    """采购经理签字：DRAFT → PENDING_VALIO_CONFIRMATION。"""
    return fx.PROTOCOL_DRAFT.model_copy(update={"status": "PENDING_VALIO_CONFIRMATION"})

@app.post("/v1/protocols/{protocol_id}/confirm", response_model=Protocol, tags=["BE1 · protocols"])
def confirm_protocol(protocol_id: str, x_demo_user: Optional[str] = DemoUser):
    """Valio 确认：→ ACTIVE，生成 jws；旧版本 → SUPERSEDED。"""
    return fx.PROTOCOL

@app.post("/v1/protocols/{protocol_id}/amend", response_model=ProtocolDiff, tags=["BE1 · protocols"])
def amend_protocol(protocol_id: str, body: ProtocolAmendRequest):
    """收紧直接生效；扩大生成新版本并要求重新签字；越权的列入 forbidden 并拒绝。"""
    return fx.PROTOCOL_DIFF

@app.post("/v1/protocols/{protocol_id}/revoke", response_model=Protocol, tags=["BE1 · protocols"])
def revoke_protocol(protocol_id: str):
    return fx.PROTOCOL.model_copy(update={"status": "REVOKED"})

@app.get("/v1/.well-known/jwks.json", response_model=Jwks, tags=["BE1 · protocols"])
def jwks():
    """Valio 的签名公钥，任何人都能用它验证协议和报价。"""
    return fx.JWKS

# ───── Profile（BE3）─────

@app.get("/v1/buyers/{buyer_id}/profiles", response_model=list[Profile], tags=["BE3 · profiles"])
def list_profiles(buyer_id: str):
    return [fx.PROFILE]

@app.post("/v1/buyers/{buyer_id}/profiles", response_model=Profile, tags=["BE3 · profiles"])
def create_profile(buyer_id: str, body: ProfileBody):
    return fx.PROFILE

@app.get("/v1/profiles/{profile_id}", response_model=Profile, tags=["BE3 · profiles"])
def get_profile(profile_id: str):
    return fx.PROFILE

@app.put("/v1/profiles/{profile_id}", response_model=Profile, tags=["BE3 · profiles"])
def update_profile(profile_id: str, body: ProfileBody):
    return fx.PROFILE

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
def parse_request(body: ParseRequest):
    """自然语言 → 预填面板。没有副作用，不建 request。"""
    return fx.PARSE_RESULT

@app.post("/v1/requests", response_model=RequestResult, tags=["BE3 · requests"])
def create_request(body: CreateRequest, x_agent_key: Optional[str] = AgentKey, x_demo_user: Optional[str] = DemoUser):
    """提交确认后的需求，跑完四个节点，返回最多 8 个候选 + 标红列表 + 处理链。"""
    return fx.REQUEST_RESULT

@app.get("/v1/requests/{request_id}", response_model=RequestResult, tags=["BE3 · requests"])
def get_request(request_id: str):
    return fx.REQUEST_RESULT

@app.post("/v1/requests/{request_id}/nodes/{node}/reject", response_model=RequestResult, tags=["BE3 · requests"])
def reject_node(request_id: str, node: int, body: RejectNodeRequest):
    """否定第 node 个节点：该节点及之后重新生成，attempt + 1。每个节点最多 3 次（否则 RETRY_LIMIT）。
    第 4 个节点只接受 SEND_TO_APPROVAL 或 PICK_OTHER。"""
    return fx.REQUEST_RESULT.model_copy(update={"attempt": 2})

@app.get("/v1/requests/{request_id}/chain", response_model=ChainTimeline, tags=["BE3 · requests"])
def get_chain(request_id: str, attempt: Optional[int] = None):
    return fx.TIMELINE

@app.post("/v1/requests/{request_id}/select", response_model=SelectResult, tags=["BE3 · requests"])
def select_candidate(request_id: str, body: SelectRequest):
    """选中一个候选：调用 BE1 quotes.create + gate.evaluate，返回签名报价和判断结果。不能选标红的。"""
    return fx.SELECT_RESULT

# ───── 报价、订单（BE1）─────

@app.get("/v1/quotes/{quote_id}", response_model=Quote, tags=["BE1 · orders"])
def get_quote(quote_id: str):
    return fx.QUOTE

@app.get("/v1/quotes/{quote_id}/verify", response_model=QuoteVerifyResult, tags=["BE1 · orders"])
def verify_quote(quote_id: str):
    return fx.QUOTE_VERIFY

_IDEMPOTENCY: dict[str, Order] = {}

@app.post("/v1/orders", response_model=Order, tags=["BE1 · orders"])
def create_order(body: CreateOrderRequest, idempotency_key: str = Header(alias="Idempotency-Key"),
                 x_agent_key: Optional[str] = AgentKey):
    """复核报价（签名、有效期、价格库存是否变化）→ 闸门 → PLACED / PENDING_* / REQUOTE_REQUIRED / REJECTED。
    同一个 Idempotency-Key 返回同一张订单。"""
    return _IDEMPOTENCY.setdefault(idempotency_key, fx.ORDER)

@app.get("/v1/orders/{order_id}", response_model=Order, tags=["BE1 · orders"])
def get_order(order_id: str):
    return fx.ORDER

# ───── 审批（BE1）─────

@app.get("/v1/approvals", response_model=list[ApprovalCard], tags=["BE1 · approvals"])
def list_approvals(kind: Optional[Literal["BUYER", "VALIO"]] = None,
                   status: Optional[Literal["PENDING", "APPROVED", "APPROVED_WITH_CHANGES", "REJECTED", "ROUTED"]] = None):
    return [fx.APPROVAL_BUYER, fx.APPROVAL_VALIO]

@app.get("/v1/approvals/{approval_id}", response_model=ApprovalCard, tags=["BE1 · approvals"])
def get_approval(approval_id: str):
    return fx.APPROVAL_BUYER

@app.post("/v1/approvals/{approval_id}/decision", response_model=ApprovalDecisionResult, tags=["BE1 · approvals"])
def decide(approval_id: str, body: ApprovalDecision, x_demo_user: Optional[str] = DemoUser):
    """批准后先复核报价：没变 → 下单；变了 → REQUOTE_REQUIRED（附新报价，超阈值则生成新审批）。"""
    return ApprovalDecisionResult(approval=fx.APPROVAL_BUYER.model_copy(update={"status": "APPROVED"}),
                                  order=fx.ORDER.model_copy(update={"quote_id": "quo_3"}))

# ───── 审计（BE1）─────

@app.get("/v1/audit", response_model=list[AuditEntry], tags=["BE1 · audit"])
def audit(request_id: Optional[str] = None, x_demo_user: Optional[str] = DemoUser):
    """按角色过滤：买家只看到 visibility 为 BUYER / BOTH 的条目。"""
    return fx.AUDIT

@app.get("/v1/audit/verify", response_model=AuditVerifyResult, tags=["BE1 · audit"])
def audit_verify():
    return fx.AUDIT_VERIFY

# ───── 管理（BE2 / BE1）─────

@app.get("/v1/admin/faults", response_model=list[Fault], tags=["BE2 · admin"])
def list_faults():
    return fx.FAULTS

@app.put("/v1/admin/faults/{name}", response_model=Fault, tags=["BE2 · admin"])
def set_fault(name: Literal["PRICE_CHANGE", "PARTIAL_STOCK", "TIMEOUT", "RATE_LIMIT", "EMPTY_RESULT"], body: FaultUpdate):
    return Fault(name=name, enabled=body.enabled, params=body.params)

@app.post("/v1/admin/reset", tags=["BE1 · admin"])
def reset():
    """清空业务表并写入演示种子数据。"""
    return {"ok": True}
