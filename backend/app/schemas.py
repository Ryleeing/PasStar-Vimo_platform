"""PasStar · API contract (Pydantic v2)

Every HTTP request and response uses the models here. Announce changes in the team chat first; BE1 merges them.
Amounts are always integer cents (_cents), times are ISO 8601, delivery days are dates only.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

# ───────────────────────── Common ─────────────────────────

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
    """One hard requirement or attribute. CLAIM = structured claim (e.g. FREE_FROM_LACTOSE);
    ALLERGEN_FREE = must explicitly declare free from an allergen (GS1 code, e.g. AN nuts); LABEL = certification label."""
    kind: Literal["CLAIM", "ALLERGEN_FREE", "LABEL"]
    code: str
    label: Optional[str] = None  # human-readable name, e.g. "Lactose-free"


# ───────────────────────── Authorisation protocol (BE1) ─────────────────────────

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
    """The approver's Ed25519 public key (JWK). With external approval, the buyer's system signs the authorisation token with the matching private key."""
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
    # SEPARATE: whoever makes a request cannot approve their own order (hotels, hospitals, chain kitchens)
    # SELF: the owner is also the buyer and may approve their own orders, but approval is still a separate, logged step (small restaurants)
    approver: str                      # approver's user ID; in SELF mode usually the person ordering
    approval_channel: Literal["HOSTED", "EXTERNAL"] = "HOSTED"
    # HOSTED: approved on PasStar's approval page (the order goes to PENDING_BUYER_APPROVAL)
    # EXTERNAL: approved in the buyer's own system; the agent comes back with a signed authorisation token to order (BUYER_AUTHORIZATION_REQUIRED)
    approver_public_key: Optional[ApproverKey] = None   # required for EXTERNAL
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
    description: str  # the procurement manager's one-sentence description


class ProtocolUpdateRequest(BaseModel):
    body: ProtocolBody


class FieldChange(BaseModel):
    field: str
    before: Any
    after: Any


class ProtocolDiff(BaseModel):
    tightened: list[FieldChange] = []   # takes effect immediately
    expanded: list[FieldChange] = []    # needs the approver to re-sign
    forbidden: list[FieldChange] = []   # the buyer has no right to approve; refused
    result_status: ProtocolStatus
    approval_id: Optional[str] = None


class ProtocolAmendRequest(BaseModel):
    changes: dict[str, Any]  # a subset of ProtocolBody fields


# ───────────────────────── Profile (BE3) ─────────────────────────

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


# ───────────────────────── Catalogue (BE2) ─────────────────────────

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
    """L0 fields only."""
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


# ───────────────────────── Requests and processing chain (BE3) ─────────────────────────

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


class SeveralItem(BaseModel):
    product: str                      # e.g. "cream"
    text: str                         # the words that asked for it, e.g. "20 litres of cream"


class ParseResult(BaseModel):
    structured: StructuredRequest     # used to prefill the checkbox panel
    ambiguities: list[Ambiguity] = []
    profile_applied: list[str] = []   # e.g. ["Same-category substitutes accepted by default"]
    # Several products in one message: one at a time for now (orchestrator/several.py); the form shows the first.
    several: list[SeveralItem] = []


class CreateRequest(BaseModel):
    protocol_id: str
    profile_id: Optional[str] = None
    input_mode: Literal["NATURAL_LANGUAGE", "PANEL"]
    raw_text: Optional[str] = None
    structured: StructuredRequest     # the structured request after the user confirmed it


class EvidenceRef(BaseModel):
    source: Literal["BUYER_INPUT", "PROFILE", "PROTOCOL", "CATALOG", "VALIO_PRICE_API", "VALIO_STOCK_API", "RULE"]
    detail: str
    trust_grade: Optional[TrustGrade] = None


class ChainEvent(BaseModel):
    at: datetime
    title: str          # what was done (plain language)
    logic: str          # why it was done (plain language)
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
    reject_options: list[str]  # rejection reason codes available for this node
    events: list[ChainEvent] = []


RejectReason = Literal[
    # ① Understand the request
    "WRONG_QUANTITY", "WRONG_DATE", "WRONG_ATTRIBUTES", "WRONG_CATEGORY",
    # ② Filter products
    "TOO_STRICT", "TOO_LOOSE", "ALLOW_BRAND", "EXCLUDE_PRODUCT",
    # ③ Combine and rank
    "WRONG_PRIORITY", "EXCLUDE_OPTION", "PREFER_SINGLE_SKU",
    # ④ Protocol check (cannot release; can only send to approval or pick another option)
    "SEND_TO_APPROVAL", "PICK_OTHER",
    "OTHER",
]


class RejectNodeRequest(BaseModel):
    reason: RejectReason
    params: dict[str, Any] = {}   # e.g. {"brand": "Arla"}, {"weights": {...}}, {"structured": {...}}
    note: Optional[str] = None


# ───────────────────────── Candidates and cards (BE2 makes the cards, BE3 assembles) ─────────────────────────

class CandidateLine(BaseModel):
    product_id: str
    name: str
    brand: Optional[str] = None
    sales_unit: str
    sales_units: float                # how many sales units to order
    qty_covered: Quantity             # the requested quantity this covers
    overbuy: Optional[Quantity] = None
    conversion_source: Literal["UNIT_CONVERSIONS", "NAME_PARSE", "UNKNOWN"]
    unit_price_cents: Optional[int] = None
    line_net_cents: Optional[int] = None
    vat_rate: Optional[float] = None
    deposit_cents: int = 0
    available_on_date: Literal["YES", "PARTIAL", "NO", "UNKNOWN"]
    deliverable_sales_units: Optional[float] = None  # only given for PARTIAL, and never more than requested
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
    overall: Level                    # the lowest of the four
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
    notes: list[str] = []             # e.g. "Lactose-free ≠ dairy-free"


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
    rule: str                          # e.g. PROFILE_EXCLUDED_BRAND, ALLERGEN_CONTAINS
    detail: str
    product_id: Optional[str] = None


class Candidate(BaseModel):
    id: str
    rank: Optional[int] = None         # 1–8; empty for blocked ones
    badge: Optional[Literal["RECOMMENDED", "CHEAPEST", "MOST_RELIABLE"]] = None
    selectable: bool
    lines: list[CandidateLine]
    totals: Optional[Totals] = None
    delivery_date: date
    reliability_level: Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]
    outcome_preview: Outcome           # what happens if this is picked
    outcome_reasons: list[str] = []
    cards: Cards
    violations: list[Violation] = []   # only blocked ones have these
    explanation: str                   # one-sentence reason (written by the LLM from disclosable data)


class RequestResult(BaseModel):
    request_id: str
    status: Literal["CANDIDATES_READY", "NO_CANDIDATES", "QUOTED", "ORDERED", "CLOSED"]
    attempt: int
    structured: StructuredRequest
    chain: list[ChainNode]             # 4 nodes
    candidates: list[Candidate]        # at most 8, sorted by rank
    blocked: list[Candidate] = []      # blocked, cannot be selected
    no_candidate_reason: Optional[str] = None


class ChainTimeline(BaseModel):
    request_id: str
    attempt: int
    events: list[ChainEvent]


# ───────────────────────── Quotes, gate, orders (BE1) ─────────────────────────

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
    buyer_authorization: Optional[str] = None   # external approval: authorisation token signed by the buyer's system (compact JWS, alg Ed25519)


class AuthorizeOrderRequest(BaseModel):
    buyer_authorization: str                   # authorisation token signed by the buyer's system (compact JWS)


class AuthorizationRequest(BaseModel):
    """Returned with BUYER_AUTHORIZATION_REQUIRED: what the buyer's system must approve and sign. The token's payload is BuyerAuthorizationClaims."""
    protocol_id: str
    buyer_id: str
    request_id: str
    quote_id: str
    amount_gross_cents: int
    currency: Literal["EUR"] = "EUR"
    delivery_date: date
    quote_valid_until: datetime
    reason_codes: list[str]                    # why authorisation is needed, e.g. R-SPEND-APPROVAL
    reasons: list[str]                         # plain language
    approver: str                              # the approver named in the protocol
    sign_with_kid: str                         # which key to sign with


class BuyerAuthorizationClaims(BaseModel):
    """The signed contents of an authorisation token. Authorises only this quote and this amount, and is void once expired."""
    typ: Literal["buyer-authorization/v1"] = "buyer-authorization/v1"
    protocol_id: str
    buyer_id: str
    quote_id: str
    amount_gross_cents: int
    approver: str
    iat: int                                   # approval time (Unix seconds)
    exp: int                                   # expiry time (Unix seconds), at most 15 minutes


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
    authorization_request: Optional[AuthorizationRequest] = None   # only with BUYER_AUTHORIZATION_REQUIRED
    authorized_by: Optional[str] = None                            # with external approval, the approver named in the token
    created_at: datetime
    placed_at: Optional[datetime] = None


# ───────────────────────── Approvals (BE1) ─────────────────────────

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


class PlacedBy(BaseModel):
    """Who asked for the order: a person in the buyer's team, or one of its agents (ChatGPT, Claude, the ERP)."""
    kind: Literal["PERSON", "AGENT"]
    id: str
    name: str
    agent_kind: Optional[str] = None


class OrderSummary(BaseModel):
    """One row of a buyer's order list: the order and what it is for, in a form people read."""
    order: Order
    buyer_id: str
    product: str
    brand: Optional[str] = None
    quantity: Quantity
    total_gross_cents: int
    delivery_date: date
    placed_by: PlacedBy


AgentKind = Literal["CHATGPT", "CLAUDE", "ERP", "UNKNOWN"]


class AgentConnection(BaseModel):
    """An external agent as the buyer (or Valio) sees it: what it is, how it connects, its key and its protocol."""
    agent_id: str
    name: str
    buyer_id: str
    buyer_name: str
    kind: AgentKind
    status: Literal["ACTIVE", "BLOCKED"] = "ACTIVE"  # BLOCKED by Valio: refused even with a valid key
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
    reasons: list[str]                         # why approval is needed (plain language)
    reason_codes: list[str]
    risk_tags: list[RiskTag] = []
    request: Optional[StructuredRequest] = None
    raw_text: Optional[str] = None
    requested_by: Optional[str] = None       # user ID of whoever made the request
    approver: Optional[str] = None           # user ID allowed to approve (from the protocol)
    approval_mode: Optional[Literal["SEPARATE", "SELF"]] = None
    quote: Optional[Quote] = None
    candidate: Optional[Candidate] = None
    alternatives: list[Candidate] = []
    protocol_diff: Optional[ProtocolDiff] = None
    disclosure_preview: list[FieldDisclosure] = []   # Valio approval: what extra would be disclosed if approved
    agent_activity: Optional[AgentActivity] = None   # Valio approval: agent behaviour
    suggested_action: Literal["APPROVE", "REJECT", "ROUTE"]
    suggested_reason: str


class ApprovalDecision(BaseModel):
    decision: Literal["APPROVE", "APPROVE_WITH_CHANGES", "REJECT", "ROUTE"]
    changes: dict[str, Any] = {}
    route_to: Optional[str] = None
    note: Optional[str] = None


class ApprovalDecisionResult(BaseModel):
    approval: ApprovalCard
    order: Optional[Order] = None    # result of re-checking and ordering after approval


# ───────────────────────── Audit (BE1) ─────────────────────────

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


# ───────────────────────── Admin (BE2 / BE1) ─────────────────────────

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
