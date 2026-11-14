"""后端模块之间的内部接口。各模块按这里的签名实现，调用方只依赖这些签名。
M1 阶段先返回假数据，确保三个人可以并行开发。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Optional, Protocol as Iface

from .schemas import (
    AllergenCard, Candidate, CandidateLine, Cards, ChainEvent, ChainNode, GateDecision, Order,
    Profile, Protocol, ProtocolBody, Quantity, Quote, Requirement, StructuredRequest, TrustScore,
    Sensitivity, TrustGrade, Violation,
)

# ───────────── 共享内部类型 ─────────────

@dataclass
class ProductRecord:                 # products 表的一行 + 关联表，内部完整版（含 L2 字段）
    id: str
    name_en: str
    brand: Optional[str]
    vendor: str
    category: str
    sales_unit: str
    lot_size: float
    qty_per_sales_unit: Optional[float]
    qty_unit: Optional[str]
    qty_source: str
    claims: dict[str, str]           # claim -> STRUCTURED | NAME_ONLY
    allergens: dict[str, tuple[str, TrustGrade]]   # code -> (status, grade)
    labels: list[str]
    flags: list[str]
    substitutions: list[str]
    deposits: list[dict]
    delivery_reliability: Optional[float]          # L2
    co2_kg_per_kg: Optional[float]
    has_synkka: bool


@dataclass
class EffectiveRules:                # 协议 ∩ profile（只收窄）
    protocol: ProtocolBody
    hard_requirements: list[Requirement]
    excluded_brands: list[str]
    excluded_vendors: list[str]
    excluded_product_ids: list[str]
    auto_limit_cents: int
    max_cents: int
    weights: dict[str, float]
    soft: dict


@dataclass
class FilterResult:
    kept: list[ProductRecord]        # 进入候选的商品（BE3 再组合成 ≤ 8 个方案）
    blocked: list[tuple[ProductRecord, list[Violation]]]   # 标红
    dropped_count: int               # 不相关、不计入标红的（如品类不对）
    events: list[ChainEvent]


@dataclass
class Conversion:
    sales_units: float
    qty_covered: Quantity
    overbuy: Optional[Quantity]
    source: Literal["UNIT_CONVERSIONS", "NAME_PARSE", "UNKNOWN"]
    exact: bool


@dataclass
class PriceInfo:
    unit_price_cents: int
    vat_rate: float
    deposit_cents_per_unit: int


@dataclass
class AvailabilityInfo:
    status: Literal["YES", "PARTIAL", "NO", "UNKNOWN"]
    deliverable_sales_units: Optional[float]       # 已按请求量封顶


@dataclass
class Audience:
    kind: Literal["AGENT", "BUYER_USER", "VALIO_USER", "LLM"]
    buyer_id: Optional[str]
    agent_id: Optional[str]
    ceiling: Sensitivity = "L1"


@dataclass
class DisclosablePack:               # 给 LLM 写解释用：只有允许披露的字段
    data: dict
    max_sensitivity: Sensitivity     # LLM 输出继承这个等级


# ───────────── BE2 目录与数据：提供给 BE1、BE3 ─────────────

class CatalogApi(Iface):
    def get(self, product_id: str) -> ProductRecord: ...
    def filter(self, req: StructuredRequest, rules: EffectiveRules) -> FilterResult:
        """按品类和结构化声明过滤；违反硬性要求或被 profile 排除的进 blocked；
        UNDECLARED / 名称与声明冲突的保留，但在卡片里标为未验证。"""
    def official_substitutes(self, product_id: str) -> list[ProductRecord]: ...
    def same_category_alternatives(self, product: ProductRecord, rules: EffectiveRules, limit: int = 5) -> list[ProductRecord]: ...

class UnitsApi(Iface):
    def convert(self, product: ProductRecord, qty: Quantity) -> Conversion:
        """需求数量 → 销售单位数，按 lot_size 向上取整。无法换算时 source=UNKNOWN、exact=False。"""

class ValioApi(Iface):                # 所有调用都经过代理层（故障注入、缓存、重试）
    def prices(self, product_ids: list[str], buyer_id: str) -> dict[str, PriceInfo]: ...
    def availability(self, product_ids: list[str], delivery_date: date, sales_units: dict[str, float],
                     agent_id: str) -> dict[str, AvailabilityInfo]:
        """同时记录 availability_probes；同一商品探测超限时抛 PROBE_LIMIT。"""

class CardsApi(Iface):
    def allergen_card(self, lines: list[CandidateLine], rules: EffectiveRules) -> AllergenCard: ...
    def trust_score(self, lines: list[CandidateLine], req: StructuredRequest, rules: EffectiveRules,
                    gate_preview: GateDecision) -> TrustScore:
        """四个维度各自 HIGH/MEDIUM/LOW，overall 取最低。"""
    def build(self, lines: list[CandidateLine], req: StructuredRequest, rules: EffectiveRules,
              gate_preview: GateDecision) -> Cards: ...

class DisclosureApi(Iface):
    def filter(self, obj: dict, audience: Audience) -> dict:
        """按 field_policy.yaml 删掉超过 audience.ceiling 的字段；所有对外响应必须经过这里。"""
    def record(self, audience: Audience, request_id: Optional[str], fields: list[tuple[str, Sensitivity, Optional[str]]]) -> None: ...
    def pack_for_llm(self, obj: dict, audience: Audience) -> DisclosablePack: ...


# ───────────── BE1 通道与交易：提供给 BE3 ─────────────

class ProtocolsApi(Iface):
    def get_active(self, protocol_id: str, agent_id: Optional[str]) -> Protocol:
        """不是 ACTIVE、已过期、agent 不匹配都抛 PROTOCOL_INVALID。"""
    def effective_rules(self, protocol: Protocol, profile: Optional[Profile]) -> EffectiveRules:
        """合并协议和 profile：只收窄。"""

class GateApi(Iface):
    def preview(self, lines: list[CandidateLine], totals_gross_cents: int, rules: EffectiveRules,
                data_unverified: bool) -> GateDecision:
        """给每个候选算“选它会怎样”，不写库。"""
    def evaluate(self, quote: Quote, rules: EffectiveRules) -> GateDecision:
        """正式判断，写审计日志。"""

class CheckpointsApi(Iface):
    def check_query(self, audience: Audience, protocol: Protocol, product_ids: list[str]) -> None: ...
    def check_response(self, audience: Audience, payload: dict) -> dict: ...
    def check_submit(self, quote: Quote, protocol: Protocol) -> None:
        """签名、有效期、报价与审批一致；不通过抛 QUOTE_EXPIRED / QUOTE_CHANGED。"""
    def check_log(self, audience: Audience, entries: list[dict]) -> list[dict]: ...

class QuotesApi(Iface):
    def create(self, request_id: str, candidate: Candidate, protocol: Protocol) -> Quote:
        """重新调用 BE2 查价格和库存，签名，15 分钟有效。"""
    def recheck(self, quote: Quote) -> tuple[bool, Optional[Quote]]:
        """下单前复核：没变返回 (True, None)；变了返回 (False, 新报价)。"""

class OrdersApi(Iface):
    def place(self, quote_id: str, protocol_id: str, idempotency_key: str, audience: Audience) -> Order: ...

class AuditApi(Iface):
    def append(self, event: str, actor: str, payload: dict, request_id: Optional[str] = None,
               visibility: Literal["BUYER", "VALIO", "BOTH"] = "BOTH", summary: str = "",
               buyer_id: Optional[str] = None, agent_id: Optional[str] = None,
               reason_code: Optional[str] = None) -> int:
        """When buyer_id / agent_id / reason_code are given, also copy them into the payload
        (keys "buyer_id", "agent_id", "reason_code") so the hash covers them."""


# ───────────── BE3 编排与 AI：提供给 BE1 ─────────────

class ParserApi(Iface):
    def parse(self, text: str, profile: Optional[Profile]) -> tuple[StructuredRequest, list[dict], list[str]]:
        """返回（结构化需求，歧义列表，已套用的 profile 项）。LLM 只看到原话和 profile。"""
    def draft_protocol(self, buyer_id: str, agent_id: str, description: str) -> tuple[ProtocolBody, list[dict]]:
        """返回（协议草稿，待确认问题）。"""

class PipelineApi(Iface):
    def run(self, request_id: str, from_node: int = 1, constraints: Optional[list[dict]] = None) -> None:
        """依次跑节点 from_node..4，写 chain_nodes 和 candidates；attempt + 1。"""

class ExplainApi(Iface):
    def node_summary(self, node: int, pack: DisclosablePack) -> str: ...
    def candidate_explanation(self, pack: DisclosablePack) -> str: ...
    def explain_attempt(self, nodes: dict[int, DisclosablePack],
                        candidates: list[DisclosablePack]) -> tuple[dict[int, str], list[str]]:
        """All texts of one attempt in one go (one LLM call): node summaries and candidate explanations."""
