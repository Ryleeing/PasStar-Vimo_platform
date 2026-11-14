"""Internal interfaces shared by backend modules.

Implementations follow these signatures so callers depend on the contracts.
M1 uses fixture responses to let the three backend workstreams develop in parallel.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, Optional, Protocol as Iface

from .schemas import (
    AllergenCard, Candidate, CandidateLine, Cards, ChainEvent, ChainNode, GateDecision, Order,
    Profile, ProductSearchRequest, Protocol, ProtocolBody, Quantity, Quote, Requirement,
    StructuredRequest, TrustScore,
    Sensitivity, TrustGrade, Violation,
)

# ───────────── Shared internal types ─────────────

@dataclass
class ProductRecord:                 # Internal product row and related facts, including L2 fields.
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
    base_unit: Optional[str] = None
    country: Optional[str] = None
    gtin: Optional[str] = None
    synkka_updated_at: Optional[datetime] = None
    case_volume_l: Optional[float] = None
    min_temp_c: Optional[float] = None
    max_temp_c: Optional[float] = None
    temperature_condition: Optional[int] = None
    has_claim_data: bool = False
    allergen_list_present: bool = False


@dataclass
class EffectiveRules:                # Protocol/profile intersection; restrictions can only tighten.
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
    kept: list[ProductRecord]        # Eligible products; BE3 assembles at most eight options.
    blocked: list[tuple[ProductRecord, list[Violation]]]   # Explicitly blocked products.
    dropped_count: int               # Irrelevant/nonmatching products, excluded without blocking.
    events: list[ChainEvent]


@dataclass
class Conversion:
    sales_units: float
    qty_covered: Optional[Quantity]  # None when no defensible conversion exists
    overbuy: Optional[Quantity]
    source: Literal["UNIT_CONVERSIONS", "NAME_PARSE", "UNKNOWN"]
    exact: bool


@dataclass
class PriceInfo:
    unit_price_cents: int
    vat_rate: float  # Fraction, e.g. 0.135; Valio's taxRate percent is normalised by its client.
    deposit_cents_per_unit: int
    unit_gross_cents: Optional[int] = None  # Preserve the upstream tax-inclusive unit price.


@dataclass
class AvailabilityInfo:
    status: Literal["YES", "PARTIAL", "NO", "UNKNOWN"]
    deliverable_sales_units: Optional[float]       # Capped at the requested quantity.


@dataclass
class Audience:
    kind: Literal["AGENT", "BUYER_USER", "VALIO_USER", "LLM"]
    buyer_id: Optional[str]
    agent_id: Optional[str]
    ceiling: Sensitivity = "L1"


@dataclass
class DisclosablePack:               # Only permitted fields for LLM-generated explanations.
    data: dict
    max_sensitivity: Sensitivity     # LLM output inherits this sensitivity level.


# ───────────── BE2 catalogue/data interfaces for BE1 and BE3 ─────────────

class CatalogApi(Iface):
    def get(self, product_id: str) -> ProductRecord: ...
    def search(self, request: ProductSearchRequest) -> list[ProductRecord]: ...
    def filter(self, req: StructuredRequest, rules: EffectiveRules) -> FilterResult:
        """Filter by category and structured declarations.

        Hard violations and profile exclusions enter blocked. Retain UNDECLARED
        evidence and name/declaration conflicts as unverified card facts.
        """
    def official_substitutes(self, product_id: str) -> list[ProductRecord]: ...
    def same_category_alternatives(self, product: ProductRecord, rules: EffectiveRules, limit: int = 5) -> list[ProductRecord]: ...

class UnitsApi(Iface):
    def convert(self, product: ProductRecord, qty: Quantity) -> Conversion:
        """Convert demand to sales units and round up to lot_size.

        Unsupported conversions return source=UNKNOWN and exact=False.
        """

class ValioApi(Iface):                # All calls use the proxy for faults, caching and retries.
    def prices(self, product_ids: list[str], buyer_id: str, *,
               delivery_date: Optional[date] = None) -> dict[str, PriceInfo]:
        """Query buyer-scoped prices for the requested delivery date."""
    def availability(self, product_ids: list[str], delivery_date: date, sales_units: dict[str, float],
                     agent_id: str) -> dict[str, AvailabilityInfo]:
        """Record availability_probes and raise PROBE_LIMIT when a product exceeds its limit."""

class CardsApi(Iface):
    def allergen_card(self, lines: list[CandidateLine], rules: EffectiveRules) -> AllergenCard: ...
    def trust_score(self, lines: list[CandidateLine], req: StructuredRequest, rules: EffectiveRules,
                    gate_preview: GateDecision) -> TrustScore:
        """Grade four dimensions HIGH/MEDIUM/LOW; overall is the lowest grade."""
    def build(self, lines: list[CandidateLine], req: StructuredRequest, rules: EffectiveRules,
              gate_preview: GateDecision) -> Cards: ...

class DisclosureApi(Iface):
    def filter(self, obj: dict, audience: Audience, *,
               request_id: Optional[str] = None) -> dict:
        """Apply field_policy.yaml and remove fields above audience.ceiling.

        Every external response must pass through this filter.
        """
    def record(self, audience: Audience, request_id: Optional[str], fields: list[tuple[str, Sensitivity, Optional[str]]]) -> None: ...
    def pack_for_llm(self, obj: dict, audience: Audience, *,
                     request_id: Optional[str] = None) -> DisclosablePack: ...


# ───────────── BE1 channel/transaction interfaces for BE3 ─────────────

class ProtocolsApi(Iface):
    def get_active(self, protocol_id: str, agent_id: Optional[str]) -> Protocol:
        """Raise PROTOCOL_INVALID for inactive, expired or agent-mismatched protocols."""
    def effective_rules(self, protocol: Protocol, profile: Optional[Profile]) -> EffectiveRules:
        """Merge protocol and profile restrictions without widening authorization."""

class GateApi(Iface):
    def preview(self, lines: list[CandidateLine], totals_gross_cents: int, rules: EffectiveRules,
                data_unverified: bool) -> GateDecision:
        """Preview the outcome of selecting a candidate without database writes."""
    def evaluate(self, quote: Quote, rules: EffectiveRules) -> GateDecision:
        """Evaluate the final gate decision and write an audit record."""

class CheckpointsApi(Iface):
    def check_query(self, audience: Audience, protocol: Protocol, product_ids: list[str]) -> None: ...
    def check_response(self, audience: Audience, payload: dict) -> dict: ...
    def check_submit(self, quote: Quote, protocol: Protocol) -> None:
        """Check signatures, expiry and quote/approval consistency.

        Raise QUOTE_EXPIRED or QUOTE_CHANGED when validation fails.
        """
    def check_log(self, audience: Audience, entries: list[dict]) -> list[dict]: ...

class QuotesApi(Iface):
    def create(self, request_id: str, candidate: Candidate, protocol: Protocol) -> Quote:
        """Refresh BE2 price/stock and sign a quote valid for 15 minutes."""
    def recheck(self, quote: Quote) -> tuple[bool, Optional[Quote]]:
        """Recheck before ordering: return (True, None) or (False, replacement_quote)."""

class OrdersApi(Iface):
    def place(self, quote_id: str, protocol_id: str, idempotency_key: str, audience: Audience) -> Order: ...

class AuditApi(Iface):
    def append(self, event: str, actor: str, payload: dict, request_id: Optional[str] = None,
               visibility: Literal["BUYER", "VALIO", "BOTH"] = "BOTH", summary: str = "") -> int: ...


# ───────────── BE3 orchestration/AI interfaces for BE1 ─────────────

class ParserApi(Iface):
    def parse(self, text: str, profile: Optional[Profile]) -> tuple[StructuredRequest, list[dict], list[str]]:
        """Return structured demand, ambiguities and applied profile items.

        The LLM sees only the original text and profile.
        """
    def draft_protocol(self, buyer_id: str, agent_id: str, description: str) -> tuple[ProtocolBody, list[dict]]:
        """Return a protocol draft and questions requiring confirmation."""

class PipelineApi(Iface):
    def run(self, request_id: str, from_node: int = 1, constraints: Optional[list[dict]] = None) -> None:
        """Run nodes from_node through 4, write nodes/candidates and increment attempt."""

class ExplainApi(Iface):
    def node_summary(self, node: int, pack: DisclosablePack) -> str: ...
    def candidate_explanation(self, pack: DisclosablePack) -> str: ...
