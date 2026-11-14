"""PasStar · 接口契约（Pydantic v2）

所有 HTTP 请求和响应都用这里的模型。改动前先在群里说，BE1 负责合并。
金额一律用整数分（_cents），时间用 ISO 8601，交货日只用日期。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

# ───────────────────────── 通用 ─────────────────────────

Level = Literal["HIGH", "MEDIUM", "LOW"]
TrustGrade = Literal["A", "B", "C", "D"]
Sensitivity = Literal["L0", "L1", "L2", "L3"]
Outcome = Literal["AUTO", "BUYER_APPROVAL", "VALIO_REVIEW", "REJECT"]
SubstitutionChoice = Literal["NONE", "SAME_CATEGORY", "ANY"]
SubstitutionTier = Literal["NONE", "OFFICIAL", "SAME_CATEGORY", "CROSS_CATEGORY"]
PolicyAction = Literal["AUTO", "APPROVAL", "FORBIDDEN"]


class Quantity(BaseModel):
    value: float = Field(gt=0)
    unit: Literal["L", "kg", "pcs"]


class ErrorDetail(BaseModel):
    code: Literal[
        "VALIDATION_ERROR", "PROTOCOL_INVALID", "OUT_OF_SCOPE", "RATE_LIMITED", "PROBE_LIMIT",
        "QUOTE_EXPIRED", "QUOTE_CHANGED", "RETRY_LIMIT", "NOT_FOUND", "UPSTREAM_ERROR",
        "SELF_APPROVAL_FORBIDDEN", "NOT_APPROVER", "AUTHORIZATION_INVALID",
    ]
    message: str
    details: dict[str, Any] = {}


class ErrorBody(BaseModel):
    error: ErrorDetail


class Requirement(BaseModel):
    """一条硬性要求或属性。CLAIM=结构化声明（如 FREE_FROM_LACTOSE）；
    ALLERGEN_FREE=必须明确声明不含某过敏原（GS1 代码，如 AN 坚果）；LABEL=证书标签。"""
    kind: Literal["CLAIM", "ALLERGEN_FREE", "LABEL"]
    code: str
    label: Optional[str] = None  # 给人看的名字，如 "无乳糖"


# ───────────────────────── 授权协议（BE1）─────────────────────────

class SubstitutionPolicy(BaseModel):
    official: PolicyAction = "AUTO"
    same_category: PolicyAction = "APPROVAL"
    cross_category: PolicyAction = "APPROVAL"


class ValioScope(BaseModel):
    prices: Literal["OWN_ONLY"] = "OWN_ONLY"
    stock: Literal["DELIVERABLE_YES_NO"] = "DELIVERABLE_YES_NO"
    rate_limit_per_min: int = 30
    max_probes_per_product_per_day: int = 3
    sensitivity_ceiling: Sensitivity = "L1"


class ApproverKey(BaseModel):
    """审批人的 Ed25519 公钥（JWK）。外部审批时，买方系统用对应的私钥签授权凭证。"""
    kty: Literal["OKP"] = "OKP"
    crv: Literal["Ed25519"] = "Ed25519"
    kid: str
    x: str


class ProtocolBody(BaseModel):
    buyer_id: str
    agent_id: str
    auto_approve_limit_cents: int
    max_order_cents: int
    allowed_categories: list[str]
    hard_requirements: list[Requirement] = []
    unknown_data_policy: Literal["REQUIRE_APPROVAL", "REJECT"] = "REQUIRE_APPROVAL"
    may_contain_policy: Literal["EXCLUDE", "REQUIRE_APPROVAL"] = "EXCLUDE"
    substitution: SubstitutionPolicy = SubstitutionPolicy()
    approval_mode: Literal["SEPARATE", "SELF"] = "SEPARATE"
    # SEPARATE：提需求的人不能审批自己的单（酒店、医院、连锁厨房）
    # SELF：老板兼采购，可以审批自己的单，但审批仍是单独的一步并记入日志（小餐厅）
    approver: str                      # 审批人用户 ID；SELF 模式下通常就是下单人
    approval_channel: Literal["HOSTED", "EXTERNAL"] = "HOSTED"
    # HOSTED：在 PasStar 的审批页上批（单子进 PENDING_BUYER_APPROVAL）
    # EXTERNAL：在买方自己的系统里批，agent 带着签名的授权凭证回来下单（BUYER_AUTHORIZATION_REQUIRED）
    approver_public_key: Optional[ApproverKey] = None   # EXTERNAL 时必填
    valid_until: date
    valio_scope: ValioScope = ValioScope()

    @model_validator(mode="after")
    def _external_needs_key(self) -> "ProtocolBody":
        if self.approval_channel == "EXTERNAL" and self.approver_public_key is None:
            raise ValueError("approval_channel EXTERNAL requires approver_public_key")
        return self


class OpenQuestion(BaseModel):
    field: str
    question: str
    suggested: Any = None


ProtocolStatus = Literal[
    "DRAFT", "PENDING_BUYER_SIGNATURE", "PENDING_VALIO_CONFIRMATION",
    "ACTIVE", "REVOKED", "EXPIRED", "SUPERSEDED",
]


class Protocol(BaseModel):
    id: str
    version: int
    status: ProtocolStatus
    body: ProtocolBody
    open_questions: list[OpenQuestion] = []
    jws: Optional[str] = None
    supersedes: Optional[str] = None
    created_at: datetime
    buyer_signed_at: Optional[datetime] = None
    valio_confirmed_at: Optional[datetime] = None


class ProtocolDraftRequest(BaseModel):
    buyer_id: str
    agent_id: str
    description: str  # 采购经理的一句话


class ProtocolUpdateRequest(BaseModel):
    body: ProtocolBody


class FieldChange(BaseModel):
    field: str
    before: Any
    after: Any


class ProtocolDiff(BaseModel):
    tightened: list[FieldChange] = []   # 直接生效
    expanded: list[FieldChange] = []    # 需要审批人重新签字
    forbidden: list[FieldChange] = []   # 买家无权批准，拒绝
    result_status: ProtocolStatus
    approval_id: Optional[str] = None


class ProtocolAmendRequest(BaseModel):
    changes: dict[str, Any]  # ProtocolBody 的部分字段


# ───────────────────────── Profile（BE3）─────────────────────────

class HardExclusions(BaseModel):
    brands: list[str] = []
    vendors: list[str] = []
    product_ids: list[str] = []
    requirements: list[Requirement] = []


class SoftPreferences(BaseModel):
    labels: list[str] = []
    countries: list[str] = []
    prefer_large_packs: bool = False


class ProfileDefaults(BaseModel):
    delivery_address: Optional[str] = None
    lead_days: Optional[int] = None
    substitution: SubstitutionChoice = "SAME_CATEGORY"


class RankingWeights(BaseModel):
    price: float = 0.4
    fulfilment: float = 0.4
    carbon: float = 0.1
    local: float = 0.1


class ProfileBody(BaseModel):
    name: str
    hard_exclusions: HardExclusions = HardExclusions()
    soft_preferences: SoftPreferences = SoftPreferences()
    defaults: ProfileDefaults = ProfileDefaults()
    weights: RankingWeights = RankingWeights()


class Profile(BaseModel):
    id: str
    buyer_id: str
    body: ProfileBody
    updated_at: datetime


# ───────────────────────── 目录（BE2）─────────────────────────

class FacetItem(BaseModel):
    code: str
    label: str


class Facets(BaseModel):
    categories: list[FacetItem]
    claims: list[FacetItem]
    allergens: list[FacetItem]
    labels: list[FacetItem]
    units: list[Literal["L", "kg", "pcs"]] = ["L", "kg", "pcs"]


class AllergenStatus(BaseModel):
    code: str
    label: str
    status: Literal["CONTAINS", "MAY_CONTAIN", "FREE_FROM", "UNDECLARED"]
    trust_grade: TrustGrade


class ProductPublic(BaseModel):
    """只含 L0 字段。"""
    id: str
    name: str
    brand: Optional[str] = None
    category: str
    sales_unit: str
    qty_per_sales_unit: Optional[float] = None
    qty_unit: Optional[Literal["L", "kg", "pcs"]] = None
    claims: list[str] = []
    labels: list[str] = []
    allergens: list[AllergenStatus] = []
    country: Optional[str] = None
    has_manufacturer_data: bool
    data_updated_at: Optional[datetime] = None
    flags: list[str] = []


class ProductSearchRequest(BaseModel):
    category: Optional[str] = None
    requirements: list[Requirement] = []
    text: Optional[str] = None
    limit: int = Field(default=20, le=50)


# ───────────────────────── 需求与处理链（BE3）─────────────────────────

class StructuredRequest(BaseModel):
    category: str
    category_label: Optional[str] = None
    requirements: list[Requirement] = []
    quantity: Quantity
    delivery_date: date
    budget_cents: Optional[int] = None
    substitution: SubstitutionChoice = "SAME_CATEGORY"
    notes: Optional[str] = None


class Ambiguity(BaseModel):
    field: str
    question: str
    options: list[str] = []


class ParseRequest(BaseModel):
    text: str
    profile_id: Optional[str] = None


class ParseResult(BaseModel):
    structured: StructuredRequest     # 用来预填勾选面板
    ambiguities: list[Ambiguity] = []
    profile_applied: list[str] = []   # 例如 ["默认接受同品类替代"]


class CreateRequest(BaseModel):
    protocol_id: str
    profile_id: Optional[str] = None
    input_mode: Literal["NATURAL_LANGUAGE", "PANEL"]
    raw_text: Optional[str] = None
    structured: StructuredRequest     # 用户确认后的结构化需求


class EvidenceRef(BaseModel):
    source: Literal["BUYER_INPUT", "PROFILE", "PROTOCOL", "CATALOG", "VALIO_PRICE_API", "VALIO_STOCK_API", "RULE"]
    detail: str
    trust_grade: Optional[TrustGrade] = None


class ChainEvent(BaseModel):
    at: datetime
    title: str          # 做了什么（人话）
    logic: str          # 为什么这样做（人话）
    evidence: list[EvidenceRef] = []


NodeName = Literal["UNDERSTAND", "FILTER", "RANK", "GATE"]


class ChainNode(BaseModel):
    node: int = Field(ge=1, le=4)
    name: NodeName
    attempt: int
    status: Literal["OK", "REJECTED", "SUPERSEDED"]
    summary: str
    can_reject: bool
    retries_left: int
    reject_options: list[str]  # 这个节点可选的否定原因代码
    events: list[ChainEvent] = []


RejectReason = Literal[
    # ① 理解需求
    "WRONG_QUANTITY", "WRONG_DATE", "WRONG_ATTRIBUTES", "WRONG_CATEGORY",
    # ② 筛选商品
    "TOO_STRICT", "TOO_LOOSE", "ALLOW_BRAND", "EXCLUDE_PRODUCT",
    # ③ 组合和排序
    "WRONG_PRIORITY", "EXCLUDE_OPTION", "PREFER_SINGLE_SKU",
    # ④ 协议判断（不能放行，只能转审批或换方案）
    "SEND_TO_APPROVAL", "PICK_OTHER",
    "OTHER",
]


class RejectNodeRequest(BaseModel):
    reason: RejectReason
    params: dict[str, Any] = {}   # 如 {"brand": "Arla"}、{"weights": {...}}、{"structured": {...}}
    note: Optional[str] = None


# ───────────────────────── 候选与卡片（BE2 出卡片，BE3 组装）─────────────────────────

class CandidateLine(BaseModel):
    product_id: str
    name: str
    brand: Optional[str] = None
    sales_unit: str
    sales_units: float                # 订购多少个销售单位
    qty_covered: Quantity             # 折合的需求数量
    overbuy: Optional[Quantity] = None
    conversion_source: Literal["UNIT_CONVERSIONS", "NAME_PARSE", "UNKNOWN"]
    unit_price_cents: Optional[int] = None
    line_net_cents: Optional[int] = None
    vat_rate: Optional[float] = None
    deposit_cents: int = 0
    available_on_date: Literal["YES", "PARTIAL", "NO", "UNKNOWN"]
    deliverable_sales_units: Optional[float] = None  # 只有 PARTIAL 时给，且不超过请求量
    substitution_tier: SubstitutionTier = "NONE"
    substitutes_for: Optional[str] = None


class Totals(BaseModel):
    net_cents: int
    vat_cents: int
    deposit_cents: int
    gross_cents: int


class TrustPoints(BaseModel):
    """0-100 per dimension: the average of that dimension's checks, kept inside its level's band
    (HIGH 80-100, MEDIUM 50-79, LOW 0-49), so a score never contradicts its level. overall = the lowest."""
    evidence: int
    match: int
    fulfilment: int
    compliance: int
    overall: int


class TrustScore(BaseModel):
    evidence: Level
    match: Level
    fulfilment: Level
    compliance: Level
    overall: Level                    # 四项中最低的一项
    how_to_improve: Optional[str] = None
    points: Optional[TrustPoints] = None


class AllergenRow(BaseModel):
    product_id: str
    requirement: Requirement
    status: Literal["FREE_FROM", "MAY_CONTAIN", "CONTAINS", "UNDECLARED", "CLAIMED", "NOT_CLAIMED"]
    trust_grade: TrustGrade
    source: str


class AllergenCard(BaseModel):
    rows: list[AllergenRow]
    data_updated_at: Optional[datetime] = None
    notes: list[str] = []             # 如 "无乳糖 ≠ 无奶"


class AlternativeRef(BaseModel):
    candidate_id: str
    tradeoff: str
    tier: SubstitutionTier


class Tip(BaseModel):
    kind: Literal["DEPOSIT", "ROUNDING", "STORAGE", "COLD_STORAGE_SPACE", "RELIABILITY", "CARBON", "LABEL", "QUOTE_TIMER"]
    text: str


class Cards(BaseModel):
    allergen: AllergenCard
    trust: TrustScore
    alternatives: list[AlternativeRef] = []
    tips: list[Tip] = []


class Violation(BaseModel):
    rule: str                          # 如 PROFILE_EXCLUDED_BRAND、ALLERGEN_CONTAINS
    detail: str
    product_id: Optional[str] = None


class Candidate(BaseModel):
    id: str
    rank: Optional[int] = None         # 1–8；标红的为空
    badge: Optional[Literal["RECOMMENDED", "CHEAPEST", "MOST_RELIABLE"]] = None
    selectable: bool
    lines: list[CandidateLine]
    totals: Optional[Totals] = None
    delivery_date: date
    reliability_level: Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]
    outcome_preview: Outcome           # 选它会怎样
    outcome_reasons: list[str] = []
    cards: Cards
    violations: list[Violation] = []   # 只有标红的才有
    explanation: str                   # 一句话理由（LLM 基于可披露数据写）


class RequestResult(BaseModel):
    request_id: str
    status: Literal["CANDIDATES_READY", "NO_CANDIDATES", "QUOTED", "ORDERED", "CLOSED"]
    attempt: int
    structured: StructuredRequest
    chain: list[ChainNode]             # 4 个节点
    candidates: list[Candidate]        # 最多 8 个，按 rank 排序
    blocked: list[Candidate] = []      # 标红、不能选
    no_candidate_reason: Optional[str] = None


class ChainTimeline(BaseModel):
    request_id: str
    attempt: int
    events: list[ChainEvent]


# ───────────────────────── 报价、闸门、订单（BE1）─────────────────────────

class SelectRequest(BaseModel):
    candidate_id: str


class RuleHit(BaseModel):
    rule_id: str
    description: str
    outcome: Outcome


class GateDecision(BaseModel):
    outcome: Outcome
    rules_hit: list[RuleHit]
    summary: str


class Quote(BaseModel):
    id: str
    request_id: str
    candidate_id: str
    protocol_id: str
    lines: list[CandidateLine]
    totals: Totals
    delivery_date: date
    valid_until: datetime
    status: Literal["VALID", "EXPIRED", "SUPERSEDED", "USED"]
    jws: str


class SelectResult(BaseModel):
    quote: Quote
    gate: GateDecision


class QuoteVerifyResult(BaseModel):
    quote_id: str
    signature_valid: bool
    expired: bool
    key_id: str


class CreateOrderRequest(BaseModel):
    quote_id: str
    protocol_id: str
    buyer_authorization: Optional[str] = None   # 外部审批：买方系统签的授权凭证（compact JWS，alg Ed25519）


class AuthorizeOrderRequest(BaseModel):
    buyer_authorization: str                   # 买方系统签的授权凭证（compact JWS）


class AuthorizationRequest(BaseModel):
    """BUYER_AUTHORIZATION_REQUIRED 时返回：买方系统要批准、签名的内容。凭证的 payload 是 BuyerAuthorizationClaims。"""
    protocol_id: str
    buyer_id: str
    request_id: str
    quote_id: str
    amount_gross_cents: int
    currency: Literal["EUR"] = "EUR"
    delivery_date: date
    quote_valid_until: datetime
    reason_codes: list[str]                    # 为什么需要授权，如 R-SPEND-APPROVAL
    reasons: list[str]                         # 人话
    approver: str                              # 协议里的审批人
    sign_with_kid: str                         # 用哪把钥匙签


class BuyerAuthorizationClaims(BaseModel):
    """授权凭证里签名的内容。只授权这一张报价、这个金额，过期作废。"""
    typ: Literal["buyer-authorization/v1"] = "buyer-authorization/v1"
    protocol_id: str
    buyer_id: str
    quote_id: str
    amount_gross_cents: int
    approver: str
    iat: int                                   # 批准时间（Unix 秒）
    exp: int                                   # 过期时间（Unix 秒），最长 15 分钟


OrderStatus = Literal[
    "PLACED", "PENDING_BUYER_APPROVAL", "BUYER_AUTHORIZATION_REQUIRED", "PENDING_VALIO_REVIEW",
    "REQUOTE_REQUIRED", "REJECTED", "CANCELLED",
]


class Order(BaseModel):
    id: str
    request_id: str
    quote_id: str
    status: OrderStatus
    gate: GateDecision
    approval_id: Optional[str] = None
    requote_reason: Optional[str] = None
    new_quote_id: Optional[str] = None
    authorization_request: Optional[AuthorizationRequest] = None   # 仅 BUYER_AUTHORIZATION_REQUIRED
    authorized_by: Optional[str] = None                            # 外部审批通过时，凭证里的审批人
    created_at: datetime
    placed_at: Optional[datetime] = None


# ───────────────────────── 审批（BE1）─────────────────────────

class RiskTag(BaseModel):
    kind: Literal["SPEND", "ALLERGEN", "DATA", "DELIVERY", "BEHAVIOUR", "SUBSTITUTION"]
    level: Level
    text: str


class FieldDisclosure(BaseModel):
    field: str
    sensitivity: Sensitivity


class AgentActivity(BaseModel):
    agent_id: str
    name: str
    requests_24h: int
    blocked_24h: int
    probe_alerts_24h: int


AgentKind = Literal["CHATGPT", "CLAUDE", "ERP", "UNKNOWN"]


class AgentConnection(BaseModel):
    """An external agent as the buyer (or Valio) sees it: what it is, how it connects, its key and its protocol."""
    agent_id: str
    name: str
    buyer_id: str
    buyer_name: str
    kind: AgentKind
    connection: Literal["MCP", "REST", "NONE"]      # how it talks to PasStar
    has_key: bool
    key_hint: Optional[str] = None                  # first characters of the current key
    key_issued_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    protocol_id: Optional[str] = None               # the active protocol it acts under; None: it can do nothing
    protocol_version: Optional[int] = None
    approver: Optional[str] = None                  # who may issue or revoke its key
    activity: AgentActivity


class IssueKeyRequest(BaseModel):
    kind: AgentKind


class IssuedKey(BaseModel):
    """Returned once. PasStar keeps only the hash; a lost key is replaced, never shown again."""
    agent_id: str
    key: str
    key_hint: str
    kind: AgentKind
    protocol_id: str


class ApprovalCard(BaseModel):
    id: str
    kind: Literal["BUYER", "VALIO"]
    subject: Literal["ORDER", "PROTOCOL_CHANGE", "DATA_ACCESS"]
    status: Literal["PENDING", "APPROVED", "APPROVED_WITH_CHANGES", "REJECTED", "ROUTED"]
    created_at: datetime
    title: str
    reasons: list[str]                         # 为什么需要审批（人话）
    reason_codes: list[str]
    risk_tags: list[RiskTag] = []
    request: Optional[StructuredRequest] = None
    raw_text: Optional[str] = None
    requested_by: Optional[str] = None       # 提需求的用户 ID
    approver: Optional[str] = None           # 有权审批的用户 ID（来自协议）
    approval_mode: Optional[Literal["SEPARATE", "SELF"]] = None
    quote: Optional[Quote] = None
    candidate: Optional[Candidate] = None
    alternatives: list[Candidate] = []
    protocol_diff: Optional[ProtocolDiff] = None
    disclosure_preview: list[FieldDisclosure] = []   # Valio 审批：批准后会多披露什么
    agent_activity: Optional[AgentActivity] = None   # Valio 审批：agent 行为
    suggested_action: Literal["APPROVE", "REJECT", "ROUTE"]
    suggested_reason: str


class ApprovalDecision(BaseModel):
    decision: Literal["APPROVE", "APPROVE_WITH_CHANGES", "REJECT", "ROUTE"]
    changes: dict[str, Any] = {}
    route_to: Optional[str] = None
    note: Optional[str] = None


class ApprovalDecisionResult(BaseModel):
    approval: ApprovalCard
    order: Optional[Order] = None    # 批准后复核再下单的结果


# ───────────────────────── 审计（BE1）─────────────────────────

class AuditEntry(BaseModel):
    seq: int
    at: datetime
    request_id: Optional[str] = None
    actor: str
    event: str
    summary: str
    disclosed_fields: list[FieldDisclosure] = []
    payload: dict[str, Any] = {}
    prev_hash: str
    hash: str


class AuditVerifyResult(BaseModel):
    ok: bool
    entries_checked: int
    first_bad_seq: Optional[int] = None


# ───────────────────────── 管理（BE2 / BE1）─────────────────────────

class Fault(BaseModel):
    name: Literal["PRICE_CHANGE", "PARTIAL_STOCK", "TIMEOUT", "RATE_LIMIT", "EMPTY_RESULT"]
    enabled: bool
    params: dict[str, Any] = {}


class FaultUpdate(BaseModel):
    enabled: bool
    params: dict[str, Any] = {}


class Jwk(BaseModel):
    kty: Literal["OKP"] = "OKP"
    crv: Literal["Ed25519"] = "Ed25519"
    kid: str
    x: str
    use: Literal["sig"] = "sig"


class Jwks(BaseModel):
    keys: list[Jwk]
