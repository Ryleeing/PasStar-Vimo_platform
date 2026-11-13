"""假数据：M1 阶段所有接口都先返回这些对象。
商品编码和单位来自真实目录；价格是编的，等接上 Valio API 后替换。"""
from datetime import date, datetime, timedelta, timezone

from .schemas import *  # noqa: F401,F403

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 11, 14, 10, 0, 0, tzinfo=TZ)
MONDAY = date(2026, 11, 16)

LACTOSE_FREE = Requirement(kind="CLAIM", code="FREE_FROM_LACTOSE", label="无乳糖")
NUT_FREE = Requirement(kind="ALLERGEN_FREE", code="AN", label="不含坚果")

PROTOCOL = Protocol(
    id="prot_hotel_v1", version=1, status="ACTIVE",
    body=ProtocolBody(
        buyer_id="buy_hotel", agent_id="agt_hotel",
        auto_approve_limit_cents=100_000, max_order_cents=300_000,
        allowed_categories=["10400", "10401", "10405"],
        hard_requirements=[LACTOSE_FREE],
        approver="procurement.manager@hotel.example",
        valid_until=date(2027, 2, 13),
    ),
    jws="eyJhbGciOiJFZERTQSIsImtpZCI6InZhbGlvLTEifQ.mock.mock",
    created_at=NOW - timedelta(days=1), buyer_signed_at=NOW - timedelta(days=1),
    valio_confirmed_at=NOW - timedelta(days=1),
)

PROTOCOL_DRAFT = Protocol(
    id="prot_hotel_v2", version=2, status="DRAFT", body=PROTOCOL.body,
    open_questions=[OpenQuestion(field="hard_requirements", question="是否需要排除坚果？", suggested=False)],
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
        name="酒店宴会",
        hard_exclusions=HardExclusions(brands=["Arla"]),
        soft_preferences=SoftPreferences(countries=["fi"], prefer_large_packs=True),
        defaults=ProfileDefaults(delivery_address="Hotel Helsinki Central, loading dock", lead_days=2),
        weights=RankingWeights(price=0.3, fulfilment=0.5, carbon=0.1, local=0.1),
    ),
)

FACETS = Facets(
    categories=[FacetItem(code="10400", label="牛奶 / Milk"), FacetItem(code="10401", label="奶油 / Cream"),
                FacetItem(code="10430", label="夸克 / Quark")],
    claims=[FacetItem(code="FREE_FROM_LACTOSE", label="无乳糖"), FacetItem(code="FREE_FROM_GLUTEN", label="无麸质"),
            FacetItem(code="VEGAN", label="纯素"), FacetItem(code="LOW_FAT", label="低脂")],
    allergens=[FacetItem(code="AN", label="坚果"), FacetItem(code="AP", label="花生"),
               FacetItem(code="AM", label="牛奶"), FacetItem(code="AE", label="鸡蛋")],
    labels=[FacetItem(code="EU_ORGANIC_FARMING", label="欧盟有机"), FacetItem(code="GOODS_FROM_FINLAND_BLUE_SWAN", label="芬兰产（蓝天鹅）")],
)

PRODUCT = ProductPublic(
    id="463038", name="Valio Eila® semi skimmed milk drink 10 l novobox lactose free", brand="Valio",
    category="10400", sales_unit="KI", qty_per_sales_unit=20, qty_unit="L",
    claims=["FREE_FROM_LACTOSE", "LOW_FAT", "VEGETARIAN"], labels=[], country="fi",
    allergens=[AllergenStatus(code="AM", label="牛奶", status="CONTAINS", trust_grade="A"),
               AllergenStatus(code="AN", label="坚果", status="UNDECLARED", trust_grade="D")],
    has_manufacturer_data=True, data_updated_at=NOW - timedelta(days=40),
)

STRUCTURED = StructuredRequest(
    category="10400", category_label="牛奶", requirements=[LACTOSE_FREE],
    quantity=Quantity(value=1000, unit="L"), delivery_date=MONDAY, substitution="SAME_CATEGORY",
)

PARSE_RESULT = ParseResult(
    structured=STRUCTURED,
    ambiguities=[Ambiguity(field="requirements", question="是全脂、低脂还是脱脂？", options=["不限", "全脂", "低脂", "脱脂"])],
    profile_applied=["默认接受同品类替代", "优先大包装"],
)


def _allergen_card(pid: str, nut_status: str = "UNDECLARED") -> AllergenCard:
    return AllergenCard(
        rows=[AllergenRow(product_id=pid, requirement=LACTOSE_FREE, status="CLAIMED", trust_grade="A",
                          source="制造商结构化声明（GS1 Synkka）")],
        data_updated_at=NOW - timedelta(days=40),
        notes=["无乳糖 ≠ 无奶：含牛奶蛋白，牛奶过敏者不能用"],
    )


CAND_1 = Candidate(
    id="cand_1", rank=1, badge="RECOMMENDED", selectable=True,
    lines=[CandidateLine(product_id="463038", name="Valio Eila® 10 l novobox lactose free", brand="Valio",
                         sales_unit="KI", sales_units=50, qty_covered=Quantity(value=1000, unit="L"),
                         conversion_source="UNIT_CONVERSIONS", unit_price_cents=1760, line_net_cents=88_000,
                         vat_rate=0.135, available_on_date="YES")],
    totals=Totals(net_cents=88_000, vat_cents=11_880, deposit_cents=0, gross_cents=99_880),
    delivery_date=MONDAY, reliability_level="UNKNOWN", outcome_preview="AUTO",
    outcome_reasons=["€998.80，在自动额度 €1,000 以内", "无乳糖有制造商结构化声明"],
    cards=Cards(
        allergen=_allergen_card("463038"),
        trust=TrustScore(evidence="HIGH", match="HIGH", fulfilment="MEDIUM", compliance="HIGH", overall="MEDIUM",
                         how_to_improve="这款没有历史配送记录；选 cand_3 履约一项会是高"),
        alternatives=[AlternativeRef(candidate_id="cand_2", tradeoff="便宜 €34，但要收 1000 盒", tier="NONE")],
        tips=[Tip(kind="STORAGE", text="冷藏 2–6 °C"),
              Tip(kind="COLD_STORAGE_SPACE", text="50 箱约占 1.1 m³ 冷藏空间"),
              Tip(kind="RELIABILITY", text="这款商品没有配送记录，履约把握按“未知”处理"),
              Tip(kind="QUOTE_TIMER", text="选中后报价 15 分钟内有效")],
    ),
    explanation="10 升大包装，搬运最省事，符合“优先大包装”的偏好；价格在自动额度内。",
)

CAND_2 = Candidate(
    id="cand_2", rank=2, badge="CHEAPEST", selectable=True,
    lines=[CandidateLine(product_id="464306", name="Valio Hyvä suomalainen Arki® Eila® 1 l lactose free", brand="Valio",
                         sales_unit="ST", sales_units=1000, qty_covered=Quantity(value=1000, unit="L"),
                         conversion_source="UNIT_CONVERSIONS", unit_price_cents=85, line_net_cents=85_000,
                         vat_rate=0.135, available_on_date="YES")],
    totals=Totals(net_cents=85_000, vat_cents=11_475, deposit_cents=0, gross_cents=96_475),
    delivery_date=MONDAY, reliability_level="HIGH", outcome_preview="AUTO",
    outcome_reasons=["€964.75，在自动额度以内"],
    cards=Cards(allergen=_allergen_card("464306"),
                trust=TrustScore(evidence="HIGH", match="HIGH", fulfilment="HIGH", compliance="HIGH", overall="HIGH"),
                tips=[Tip(kind="LABEL", text="芬兰产")]),
    explanation="总价最低，历史配送记录良好；1 升小包装，需要拆箱的量比较大。",
)

CAND_3 = Candidate(
    id="cand_3", rank=3, badge="MOST_RELIABLE", selectable=True,
    lines=[CandidateLine(product_id="464201", name="Valio semi skimmed milk 1 l lactose free UHT", brand="Valio",
                         sales_unit="ST", sales_units=1000, qty_covered=Quantity(value=1000, unit="L"),
                         conversion_source="UNIT_CONVERSIONS", unit_price_cents=95, line_net_cents=95_000,
                         vat_rate=0.135, available_on_date="YES")],
    totals=Totals(net_cents=95_000, vat_cents=12_825, deposit_cents=0, gross_cents=107_825),
    delivery_date=MONDAY, reliability_level="HIGH", outcome_preview="BUYER_APPROVAL",
    outcome_reasons=["€1,078.25，超过自动额度 €1,000"],
    cards=Cards(allergen=_allergen_card("464201"),
                trust=TrustScore(evidence="HIGH", match="HIGH", fulfilment="HIGH", compliance="MEDIUM", overall="MEDIUM",
                                 how_to_improve="超过自动额度，需要经理审批"),
                tips=[Tip(kind="STORAGE", text="UHT，常温保存")]),
    explanation="UHT 常温保存、配送记录最好；但总价超过自动额度，需要审批。",
)

BLOCKED_1 = Candidate(
    id="cand_x1", selectable=False,
    lines=[CandidateLine(product_id="464179", name="Arla 10 L Novobox lactose free milk drink ESL", brand="Arla",
                         sales_unit="ST", sales_units=100, qty_covered=Quantity(value=1000, unit="L"),
                         conversion_source="UNIT_CONVERSIONS", available_on_date="UNKNOWN")],
    delivery_date=MONDAY, reliability_level="UNKNOWN", outcome_preview="REJECT",
    cards=Cards(allergen=_allergen_card("464179"),
                trust=TrustScore(evidence="HIGH", match="HIGH", fulfilment="MEDIUM", compliance="LOW", overall="LOW")),
    violations=[Violation(rule="PROFILE_EXCLUDED_BRAND", detail="profile“酒店宴会”排除了品牌 Arla", product_id="464179")],
    explanation="符合无乳糖要求，但你在 profile 里排除了这个品牌。",
)


def _chain(attempt: int = 1) -> list[ChainNode]:
    return [
        ChainNode(node=1, name="UNDERSTAND", attempt=attempt, status="OK", can_reject=True, retries_left=3,
                  reject_options=["WRONG_QUANTITY", "WRONG_DATE", "WRONG_ATTRIBUTES", "WRONG_CATEGORY", "OTHER"],
                  summary="你要的是：牛奶，无乳糖，1000 升，11 月 16 日（周一）前送到；已套用 profile“酒店宴会”。",
                  events=[ChainEvent(at=NOW, title="理解需求", logic="从你的原话里提取品类、属性、数量和日期",
                                     evidence=[EvidenceRef(source="BUYER_INPUT", detail="周一前要 1000 升无乳糖奶"),
                                               EvidenceRef(source="PROFILE", detail="酒店宴会：优先大包装，排除 Arla")])]),
        ChainNode(node=2, name="FILTER", attempt=attempt, status="OK", can_reject=True, retries_left=3,
                  reject_options=["TOO_STRICT", "TOO_LOOSE", "ALLOW_BRAND", "EXCLUDE_PRODUCT", "OTHER"],
                  summary="找到 35 个带无乳糖声明的牛奶；排除 27 个（不是液态奶、你不要的品牌）；保留 8 个。",
                  events=[ChainEvent(at=NOW + timedelta(seconds=1), title="在商品目录里筛选",
                                     logic="只用制造商的结构化声明，不靠商品名称判断",
                                     evidence=[EvidenceRef(source="CATALOG", detail="品类 10400 + FREE_FROM_LACTOSE", trust_grade="A")])]),
        ChainNode(node=3, name="RANK", attempt=attempt, status="OK", can_reject=True, retries_left=3,
                  reject_options=["WRONG_PRIORITY", "EXCLUDE_OPTION", "PREFER_SINGLE_SKU", "OTHER"],
                  summary="按你的偏好，履约最重要、价格其次；第 1 名是 10 升装，共 50 箱。",
                  events=[ChainEvent(at=NOW + timedelta(seconds=2), title="查价格和库存并排序",
                                     logic="只查询这 8 个候选，然后按 profile 的权重排序",
                                     evidence=[EvidenceRef(source="VALIO_PRICE_API", detail="8 个商品的客户价格"),
                                               EvidenceRef(source="VALIO_STOCK_API", detail="11 月 16 日能否交付")])]),
        ChainNode(node=4, name="GATE", attempt=attempt, status="OK", can_reject=True, retries_left=3,
                  reject_options=["SEND_TO_APPROVAL", "PICK_OTHER"],
                  summary="第 1 名 €998.80，在自动额度 €1,000 以内，所有要求都有明确声明：选它可以自动下单。",
                  events=[ChainEvent(at=NOW + timedelta(seconds=3), title="按协议判断", logic="逐条比对授权协议",
                                     evidence=[EvidenceRef(source="RULE", detail="R-SPEND-AUTO：金额 ≤ 自动额度")])]),
    ]


REQUEST_RESULT = RequestResult(
    request_id="req_1", status="CANDIDATES_READY", attempt=1, structured=STRUCTURED,
    chain=_chain(), candidates=[CAND_1, CAND_2, CAND_3], blocked=[BLOCKED_1],
)

TIMELINE = ChainTimeline(request_id="req_1", attempt=1,
                         events=[e for n in _chain() for e in n.events])

GATE_AUTO = GateDecision(outcome="AUTO", summary="在自动额度内，所有硬性要求都有明确声明",
                         rules_hit=[RuleHit(rule_id="R-SPEND-AUTO", description="金额 ≤ 自动额度", outcome="AUTO")])
GATE_APPROVAL = GateDecision(outcome="BUYER_APPROVAL", summary="超过自动额度，需要采购经理审批",
                             rules_hit=[RuleHit(rule_id="R-SPEND-APPROVAL", description="自动额度 < 金额 ≤ 预算上限", outcome="BUYER_APPROVAL")])

QUOTE = Quote(id="quo_1", request_id="req_1", candidate_id="cand_1", protocol_id="prot_hotel_v1",
              lines=CAND_1.lines, totals=CAND_1.totals, delivery_date=MONDAY,
              valid_until=NOW + timedelta(minutes=15), status="VALID",
              jws="eyJhbGciOiJFZERTQSIsImtpZCI6InZhbGlvLTEifQ.mock.mock")

SELECT_RESULT = SelectResult(quote=QUOTE, gate=GATE_AUTO)
QUOTE_VERIFY = QuoteVerifyResult(quote_id="quo_1", signature_valid=True, expired=False, key_id="valio-1")

ORDER = Order(id="ord_1", request_id="req_1", quote_id="quo_1", status="PLACED", gate=GATE_AUTO,
              created_at=NOW, placed_at=NOW)

APPROVAL_BUYER = ApprovalCard(
    id="apr_1", kind="BUYER", subject="ORDER", status="PENDING", created_at=NOW,
    title="1000 升无乳糖牛奶，€1,078.25",
    reasons=["金额 €1,078.25 超过自动额度 €1,000"], reason_codes=["R-SPEND-APPROVAL"],
    risk_tags=[RiskTag(kind="SPEND", level="MEDIUM", text="超出自动额度 €78.25")],
    request=STRUCTURED, raw_text="周一前要 1000 升无乳糖奶",
    quote=QUOTE.model_copy(update={"id": "quo_3", "candidate_id": "cand_3", "lines": CAND_3.lines, "totals": CAND_3.totals}),
    candidate=CAND_3, alternatives=[CAND_1, CAND_2],
    suggested_action="APPROVE", suggested_reason="超出幅度小，履约把握最高",
)

APPROVAL_VALIO = ApprovalCard(
    id="apr_9", kind="VALIO", subject="DATA_ACCESS", status="PENDING", created_at=NOW,
    title="agt_rogue 请求精确库存",
    reasons=["请求的字段属于 L2（精确库存），超出协议范围", "过去 10 分钟用 6 种不同数量探测同一商品"],
    reason_codes=["OUT_OF_SCOPE", "PROBE_PATTERN"],
    risk_tags=[RiskTag(kind="DATA", level="HIGH", text="会披露 L2 字段：仓库库存数量"),
               RiskTag(kind="BEHAVIOUR", level="HIGH", text="疑似二分法探测库存")],
    disclosure_preview=[FieldDisclosure(field="stock.quantity", sensitivity="L2")],
    agent_activity=AgentActivity(agent_id="agt_rogue", name="Unknown Procurement Bot", requests_24h=212,
                                 blocked_24h=47, probe_alerts_24h=3),
    suggested_action="REJECT", suggested_reason="L2 数据，且行为符合探测特征",
)

AUDIT = [
    AuditEntry(seq=1, at=NOW, request_id="req_1", actor="agent:agt_hotel", event="REQUEST_CREATED",
               summary="收到需求：1000 升无乳糖牛奶，周一前", prev_hash="0" * 64, hash="a1" * 32),
    AuditEntry(seq=2, at=NOW + timedelta(seconds=2), request_id="req_1", actor="system", event="CANDIDATES_DISCLOSED",
               summary="向 agt_hotel 披露 8 个候选的价格（L1）和能否交付（L1）",
               disclosed_fields=[FieldDisclosure(field="price.net", sensitivity="L1"),
                                 FieldDisclosure(field="availability.deliverable", sensitivity="L1")],
               prev_hash="a1" * 32, hash="b2" * 32),
    AuditEntry(seq=3, at=NOW + timedelta(seconds=5), request_id="req_1", actor="system", event="ORDER_PLACED",
               summary="规则 R-SPEND-AUTO 放行，订单 ord_1 已下单", prev_hash="b2" * 32, hash="c3" * 32),
]

AUDIT_VERIFY = AuditVerifyResult(ok=True, entries_checked=3)

FAULTS = [Fault(name=n, enabled=False) for n in ("PRICE_CHANGE", "PARTIAL_STOCK", "TIMEOUT", "RATE_LIMIT", "EMPTY_RESULT")]

JWKS = Jwks(keys=[Jwk(kid="valio-1", x="11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo")])
