"""Mock data (M1): every endpoint returns these objects first.
Product IDs and units are real; prices are made up until the Valio API is connected."""
from datetime import date, datetime, timedelta, timezone

from . import demo_keys
from .schemas import *  # noqa: F401,F403

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 11, 14, 10, 0, 0, tzinfo=TZ)
MONDAY = date(2026, 11, 16)

LACTOSE_FREE = Requirement(kind="CLAIM", code="FREE_FROM_LACTOSE", label="Lactose-free")
NUT_FREE = Requirement(kind="ALLERGEN_FREE", code="AN", label="Nut-free")

PROTOCOL = Protocol(
    id="prot_hotel_v1", version=1, status="ACTIVE",
    body=ProtocolBody(
        buyer_id="buy_hotel", agent_id="agt_hotel",
        auto_approve_limit_cents=100_000, max_order_cents=300_000,
        allowed_categories=["10400", "10401", "10405"],
        hard_requirements=[LACTOSE_FREE],
        approval_mode="SEPARATE",
        approver="usr_manager",
        valid_until=date(2027, 2, 13),
    ),
    jws="eyJhbGciOiJFZERTQSIsImtpZCI6InZhbGlvLTEifQ.mock.mock",
    created_at=NOW - timedelta(days=1), buyer_signed_at=NOW - timedelta(days=1),
    valio_confirmed_at=NOW - timedelta(days=1),
)

# External approval: the hotel's own purchasing agent (agt_hotel_ext); over-limit orders are approved in the hotel's own system
PROTOCOL_EXTERNAL = Protocol(
    id="prot_hotel_ext_v1", version=1, status="ACTIVE",
    body=PROTOCOL.body.model_copy(update={
        "agent_id": "agt_hotel_ext", "approval_channel": "EXTERNAL",
        "approver_public_key": ApproverKey(**demo_keys.public_jwk()),
    }),
    jws="eyJhbGciOiJFZERTQSIsImtpZCI6InZhbGlvLTEifQ.mock.mock",
    created_at=NOW - timedelta(days=1), buyer_signed_at=NOW - timedelta(days=1),
    valio_confirmed_at=NOW - timedelta(days=1),
)

PROTOCOL_DRAFT = Protocol(
    id="prot_hotel_v2", version=2, status="DRAFT", body=PROTOCOL.body,
    open_questions=[OpenQuestion(field="hard_requirements", question="Should nuts be excluded?", suggested=False)],
    supersedes="prot_hotel_v1", created_at=NOW,
)

PROTOCOL_DIFF = ProtocolDiff(
    tightened=[FieldChange(field="auto_approve_limit_cents", before=100_000, after=80_000)],
    expanded=[FieldChange(field="allowed_categories", before=["10400"], after=["10400", "22540"])],
    forbidden=[FieldChange(field="valio_scope.prices", before="OWN_ONLY", after="ALL_CUSTOMERS")],
    result_status="PENDING_BUYER_SIGNATURE", approval_id="apr_prot_1",
)

PROFILE = Profile(
    id="prof_banquet", buyer_id="buy_hotel", updated_at=NOW,
    body=ProfileBody(
        name="Hotel banquets",
        hard_exclusions=HardExclusions(brands=["Arla"]),
        soft_preferences=SoftPreferences(countries=["fi"], prefer_large_packs=True),
        defaults=ProfileDefaults(delivery_address="Hotel Helsinki Central, loading dock", lead_days=2),
        weights=RankingWeights(price=0.3, fulfilment=0.5, carbon=0.1, local=0.1),
    ),
)

FACETS = Facets(
    categories=[FacetItem(code="10400", label="Milk"), FacetItem(code="10401", label="Cream"),
                FacetItem(code="10405", label="Milk powder"),
                FacetItem(code="10430", label="Quark")],
    claims=[FacetItem(code="FREE_FROM_LACTOSE", label="Lactose-free"), FacetItem(code="FREE_FROM_GLUTEN", label="Gluten-free"),
            FacetItem(code="VEGAN", label="Vegan"), FacetItem(code="LOW_FAT", label="Low fat")],
    allergens=[FacetItem(code="AN", label="Tree nuts"), FacetItem(code="AP", label="Peanuts"),
               FacetItem(code="AM", label="Milk"), FacetItem(code="AE", label="Eggs")],
    labels=[FacetItem(code="EU_ORGANIC_FARMING", label="EU organic"), FacetItem(code="GOODS_FROM_FINLAND_BLUE_SWAN", label="Made in Finland (Blue Swan)")],
)

PRODUCT = ProductPublic(
    id="463038", name="Valio Eila® semi skimmed milk drink 10 l novobox lactose free", brand="Valio",
    category="10400", sales_unit="KI", qty_per_sales_unit=20, qty_unit="L",
    claims=["FREE_FROM_LACTOSE", "LOW_FAT", "VEGETARIAN"], labels=[], country="fi",
    allergens=[AllergenStatus(code="AM", label="Milk", status="CONTAINS", trust_grade="A"),
               AllergenStatus(code="AN", label="Tree nuts", status="UNDECLARED", trust_grade="D")],
    has_manufacturer_data=True, data_updated_at=NOW - timedelta(days=40),
)

STRUCTURED = StructuredRequest(
    category="10400", category_label="Milk", requirements=[LACTOSE_FREE],
    quantity=Quantity(value=1000, unit="L"), delivery_date=MONDAY, substitution="SAME_CATEGORY",
)

PARSE_RESULT = ParseResult(
    structured=STRUCTURED,
    ambiguities=[Ambiguity(field="requirements", question="Whole, semi-skimmed or skimmed?", options=["Any", "Whole", "Semi-skimmed", "Skimmed"])],
    profile_applied=["Same-category substitutes allowed by default", "Prefer large packs"],
)


def _allergen_card(pid: str, nut_status: str = "UNDECLARED") -> AllergenCard:
    return AllergenCard(
        rows=[AllergenRow(product_id=pid, requirement=LACTOSE_FREE, status="CLAIMED", trust_grade="A",
                          source="Manufacturer structured claim (GS1 Synkka)")],
        data_updated_at=NOW - timedelta(days=40),
        notes=["Lactose-free is not milk-free: contains milk protein, not suitable for milk allergy"],
    )


CAND_1 = Candidate(
    id="cand_1", rank=1, badge="RECOMMENDED", selectable=True,
    lines=[CandidateLine(product_id="463038", name="Valio Eila® 10 l novobox lactose free", brand="Valio",
                         sales_unit="KI", sales_units=50, qty_covered=Quantity(value=1000, unit="L"),
                         conversion_source="UNIT_CONVERSIONS", unit_price_cents=1760, line_net_cents=88_000,
                         vat_rate=0.135, available_on_date="YES")],
    totals=Totals(net_cents=88_000, vat_cents=11_880, deposit_cents=0, gross_cents=99_880),
    delivery_date=MONDAY, reliability_level="UNKNOWN", outcome_preview="AUTO",
    outcome_reasons=["€998.80, within the auto-approve limit of €1,000", "Lactose-free backed by a manufacturer structured claim"],
    cards=Cards(
        allergen=_allergen_card("463038"),
        trust=TrustScore(evidence="HIGH", match="HIGH", fulfilment="MEDIUM", compliance="HIGH", overall="MEDIUM",
                         how_to_improve="No delivery history for this product; cand_3 scores high on fulfilment"),
        alternatives=[AlternativeRef(candidate_id="cand_2", tradeoff="€34 cheaper, but 1,000 cartons to receive", tier="NONE")],
        tips=[Tip(kind="STORAGE", text="Keep chilled at 2–6 °C"),
              Tip(kind="COLD_STORAGE_SPACE", text="50 cases take about 1.1 m³ of cold storage"),
              Tip(kind="RELIABILITY", text="No delivery history; fulfilment treated as unknown"),
              Tip(kind="QUOTE_TIMER", text="Quote is valid for 15 minutes after you select it")],
    ),
    explanation="10 L bag-in-box packs are the easiest to handle and match your large-pack preference; within the auto-approve limit.",
)

CAND_2 = Candidate(
    id="cand_2", rank=2, badge="CHEAPEST", selectable=True,
    lines=[CandidateLine(product_id="464306", name="Valio Hyvä suomalainen Arki® Eila® 1 l lactose free", brand="Valio",
                         sales_unit="ST", sales_units=1000, qty_covered=Quantity(value=1000, unit="L"),
                         conversion_source="UNIT_CONVERSIONS", unit_price_cents=85, line_net_cents=85_000,
                         vat_rate=0.135, available_on_date="YES")],
    totals=Totals(net_cents=85_000, vat_cents=11_475, deposit_cents=0, gross_cents=96_475),
    delivery_date=MONDAY, reliability_level="HIGH", outcome_preview="AUTO",
    outcome_reasons=["€964.75, within the auto-approve limit"],
    cards=Cards(allergen=_allergen_card("464306"),
                trust=TrustScore(evidence="HIGH", match="HIGH", fulfilment="HIGH", compliance="HIGH", overall="HIGH"),
                tips=[Tip(kind="LABEL", text="Made in Finland")]),
    explanation="Lowest total and a good delivery record; 1 L cartons mean more unpacking.",
)

CAND_3 = Candidate(
    id="cand_3", rank=3, badge="MOST_RELIABLE", selectable=True,
    lines=[CandidateLine(product_id="464201", name="Valio semi skimmed milk 1 l lactose free UHT", brand="Valio",
                         sales_unit="ST", sales_units=1000, qty_covered=Quantity(value=1000, unit="L"),
                         conversion_source="UNIT_CONVERSIONS", unit_price_cents=95, line_net_cents=95_000,
                         vat_rate=0.135, available_on_date="YES")],
    totals=Totals(net_cents=95_000, vat_cents=12_825, deposit_cents=0, gross_cents=107_825),
    delivery_date=MONDAY, reliability_level="HIGH", outcome_preview="BUYER_APPROVAL",
    outcome_reasons=["€1,078.25, above the auto-approve limit of €1,000"],
    cards=Cards(allergen=_allergen_card("464201"),
                trust=TrustScore(evidence="HIGH", match="HIGH", fulfilment="HIGH", compliance="MEDIUM", overall="MEDIUM",
                                 how_to_improve="Above the auto-approve limit; needs manager approval"),
                tips=[Tip(kind="STORAGE", text="UHT, store at room temperature")]),
    explanation="UHT keeps at room temperature and has the best delivery record; total is above the auto-approve limit, so it needs approval.",
)

BLOCKED_1 = Candidate(
    id="cand_x1", selectable=False,
    lines=[CandidateLine(product_id="464179", name="Arla 10 L Novobox lactose free milk drink ESL", brand="Arla",
                         sales_unit="ST", sales_units=100, qty_covered=Quantity(value=1000, unit="L"),
                         conversion_source="UNIT_CONVERSIONS", available_on_date="UNKNOWN")],
    delivery_date=MONDAY, reliability_level="UNKNOWN", outcome_preview="REJECT",
    cards=Cards(allergen=_allergen_card("464179"),
                trust=TrustScore(evidence="HIGH", match="HIGH", fulfilment="MEDIUM", compliance="LOW", overall="LOW")),
    violations=[Violation(rule="PROFILE_EXCLUDED_BRAND", detail="Brand Arla is excluded in profile 'Hotel banquets'", product_id="464179")],
    explanation="Meets the lactose-free requirement, but you excluded this brand in your profile.",
)


def _chain(attempt: int = 1) -> list[ChainNode]:
    return [
        ChainNode(node=1, name="UNDERSTAND", attempt=attempt, status="OK", can_reject=True, retries_left=3,
                  reject_options=["WRONG_QUANTITY", "WRONG_DATE", "WRONG_ATTRIBUTES", "WRONG_CATEGORY", "OTHER"],
                  summary="You need: milk, lactose-free, 1,000 L, delivered by Monday 16 Nov; profile 'Hotel banquets' applied.",
                  events=[ChainEvent(at=NOW, title="Understood the request", logic="Extracted category, attributes, quantity and date from your words",
                                     evidence=[EvidenceRef(source="BUYER_INPUT", detail="1000 litres of lactose-free milk by Monday"),
                                               EvidenceRef(source="PROFILE", detail="Hotel banquets: prefer large packs, exclude Arla")])]),
        ChainNode(node=2, name="FILTER", attempt=attempt, status="OK", can_reject=True, retries_left=3,
                  reject_options=["TOO_STRICT", "TOO_LOOSE", "ALLOW_BRAND", "EXCLUDE_PRODUCT", "OTHER"],
                  summary="Found 35 milks with a lactose-free claim; excluded 27 (not liquid milk, excluded brand); kept 8.",
                  events=[ChainEvent(at=NOW + timedelta(seconds=1), title="Filtered the catalogue",
                                     logic="Used manufacturer structured claims only, not product names",
                                     evidence=[EvidenceRef(source="CATALOG", detail="Category 10400 + FREE_FROM_LACTOSE", trust_grade="A")])]),
        ChainNode(node=3, name="RANK", attempt=attempt, status="OK", can_reject=True, retries_left=3,
                  reject_options=["WRONG_PRIORITY", "EXCLUDE_OPTION", "PREFER_SINGLE_SKU", "OTHER"],
                  summary="Ranked by your preferences: fulfilment first, then price. #1 is the 10 L pack, 50 cases.",
                  events=[ChainEvent(at=NOW + timedelta(seconds=2), title="Checked price and stock, then ranked",
                                     logic="Queried only these 8 candidates, then ranked by profile weights",
                                     evidence=[EvidenceRef(source="VALIO_PRICE_API", detail="Customer prices for 8 products"),
                                               EvidenceRef(source="VALIO_STOCK_API", detail="Deliverable on 16 Nov?")])]),
        ChainNode(node=4, name="GATE", attempt=attempt, status="OK", can_reject=True, retries_left=3,
                  reject_options=["SEND_TO_APPROVAL", "PICK_OTHER"],
                  summary="#1 is €998.80, within the €1,000 auto-approve limit, and every requirement is explicitly declared: it can be ordered automatically.",
                  events=[ChainEvent(at=NOW + timedelta(seconds=3), title="Checked against the protocol", logic="Compared rule by rule with the authorization protocol",
                                     evidence=[EvidenceRef(source="RULE", detail="R-SPEND-AUTO: total ≤ auto-approve limit")])]),
    ]


REQUEST_RESULT = RequestResult(
    request_id="req_1", status="CANDIDATES_READY", attempt=1, structured=STRUCTURED,
    chain=_chain(), candidates=[CAND_1, CAND_2, CAND_3], blocked=[BLOCKED_1],
)

TIMELINE = ChainTimeline(request_id="req_1", attempt=1,
                         events=[e for n in _chain() for e in n.events])

GATE_AUTO = GateDecision(outcome="AUTO", summary="Within the auto-approve limit; all hard requirements explicitly declared",
                         rules_hit=[RuleHit(rule_id="R-SPEND-AUTO", description="Total ≤ auto-approve limit", outcome="AUTO")])
GATE_APPROVAL = GateDecision(outcome="BUYER_APPROVAL", summary="Above the auto-approve limit; needs procurement manager approval",
                             rules_hit=[RuleHit(rule_id="R-SPEND-APPROVAL", description="Auto-approve limit < total ≤ order maximum", outcome="BUYER_APPROVAL")])

QUOTE = Quote(id="quo_1", request_id="req_1", candidate_id="cand_1", protocol_id="prot_hotel_v1",
              lines=CAND_1.lines, totals=CAND_1.totals, delivery_date=MONDAY,
              valid_until=NOW + timedelta(minutes=15), status="VALID",
              jws="eyJhbGciOiJFZERTQSIsImtpZCI6InZhbGlvLTEifQ.mock.mock")

SELECT_RESULT = SelectResult(quote=QUOTE, gate=GATE_AUTO)
QUOTE_VERIFY = QuoteVerifyResult(quote_id="quo_1", signature_valid=True, expired=False, key_id="valio-1")

ORDER = Order(id="ord_1", request_id="req_1", quote_id="quo_1", status="PLACED", gate=GATE_AUTO,
              created_at=NOW, placed_at=NOW)

# External approval example: the agent picked cand_3 (€1,078.25, over the €1,000 automatic limit)
QUOTE_EXT = QUOTE.model_copy(update={"id": "quo_7", "candidate_id": "cand_3", "protocol_id": "prot_hotel_ext_v1",
                                     "lines": CAND_3.lines, "totals": CAND_3.totals})
AUTHORIZATION_REQUEST = AuthorizationRequest(
    protocol_id="prot_hotel_ext_v1", buyer_id="buy_hotel", request_id="req_1", quote_id="quo_7",
    amount_gross_cents=CAND_3.totals.gross_cents, delivery_date=MONDAY, quote_valid_until=QUOTE_EXT.valid_until,
    reason_codes=["R-SPEND-APPROVAL"], reasons=["Total €1,078.25 is above the auto-approve limit of €1,000"],
    approver="usr_manager", sign_with_kid=demo_keys.KID,
)
ORDER_AUTH_REQUIRED = Order(id="ord_7", request_id="req_1", quote_id="quo_7", status="BUYER_AUTHORIZATION_REQUIRED",
                            gate=GATE_APPROVAL, authorization_request=AUTHORIZATION_REQUEST, created_at=NOW)

APPROVAL_BUYER = ApprovalCard(
    id="apr_1", kind="BUYER", subject="ORDER", status="PENDING", created_at=NOW,
    title="1,000 L lactose-free milk, €1,078.25",
    reasons=["Total €1,078.25 is above the auto-approve limit of €1,000"], reason_codes=["R-SPEND-APPROVAL"],
    risk_tags=[RiskTag(kind="SPEND", level="MEDIUM", text="€78.25 over the auto-approve limit")],
    request=STRUCTURED, raw_text="1000 litres of lactose-free milk by Monday",
    requested_by="usr_chef", approver="usr_manager", approval_mode="SEPARATE",
    quote=QUOTE.model_copy(update={"id": "quo_3", "candidate_id": "cand_3", "lines": CAND_3.lines, "totals": CAND_3.totals}),
    candidate=CAND_3, alternatives=[CAND_1, CAND_2],
    suggested_action="APPROVE", suggested_reason="Small overrun; highest fulfilment confidence",
)

APPROVAL_VALIO = ApprovalCard(
    id="apr_9", kind="VALIO", subject="DATA_ACCESS", status="PENDING", created_at=NOW,
    title="agt_rogue asks for exact stock levels",
    reasons=["Requested field is L2 (exact stock), outside the protocol scope", "Probed the same product with 6 different quantities in 10 minutes"],
    reason_codes=["OUT_OF_SCOPE", "PROBE_PATTERN"],
    risk_tags=[RiskTag(kind="DATA", level="HIGH", text="Would disclose an L2 field: warehouse stock quantity"),
               RiskTag(kind="BEHAVIOUR", level="HIGH", text="Looks like binary-search probing of stock")],
    disclosure_preview=[FieldDisclosure(field="stock.quantity", sensitivity="L2")],
    agent_activity=AgentActivity(agent_id="agt_rogue", name="Unknown Procurement Bot", requests_24h=212,
                                 blocked_24h=47, probe_alerts_24h=3),
    suggested_action="REJECT", suggested_reason="L2 data and the behaviour matches probing",
)

AUDIT = [
    AuditEntry(seq=1, at=NOW, request_id="req_1", actor="agent:agt_hotel", event="REQUEST_CREATED",
               summary="Request received: 1,000 L lactose-free milk by Monday", prev_hash="0" * 64, hash="a1" * 32),
    AuditEntry(seq=2, at=NOW + timedelta(seconds=2), request_id="req_1", actor="system", event="CANDIDATES_DISCLOSED",
               summary="Disclosed prices (L1) and deliverability (L1) for 8 candidates to agt_hotel",
               disclosed_fields=[FieldDisclosure(field="price.net", sensitivity="L1"),
                                 FieldDisclosure(field="availability.deliverable", sensitivity="L1")],
               prev_hash="a1" * 32, hash="b2" * 32),
    AuditEntry(seq=3, at=NOW + timedelta(seconds=5), request_id="req_1", actor="system", event="ORDER_PLACED",
               summary="Rule R-SPEND-AUTO passed; order ord_1 placed", prev_hash="b2" * 32, hash="c3" * 32),
]

AUDIT_VERIFY = AuditVerifyResult(ok=True, entries_checked=3)

FAULTS = [Fault(name=n, enabled=False) for n in ("PRICE_CHANGE", "PARTIAL_STOCK", "TIMEOUT", "RATE_LIMIT", "EMPTY_RESULT")]

JWKS = Jwks(keys=[Jwk(kid="valio-1", x="11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo")])
