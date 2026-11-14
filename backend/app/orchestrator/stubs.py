"""Local stand-ins for the BE1 / BE2 interfaces the pipeline depends on.

They follow internal.py and use a handful of real product IDs, so BE3 can build and
test the pipeline before BE1 / BE2 deliver. Replace each one with the real
implementation as it lands; the pipeline only depends on the interfaces.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Optional

from .. import fixtures as fx
from ..internal import (
    AvailabilityInfo, Audience, Conversion, DisclosablePack, EffectiveRules, FilterResult, PriceInfo,
    ProductRecord,
)
from ..catalog.cards import trust_points
from ..schemas import (
    AllergenCard, AllergenRow, CandidateLine, Cards, ChainEvent, EvidenceRef, GateDecision, Level,
    Profile, Protocol, Quantity, RuleHit, StructuredRequest, Tip, TrustScore, Violation,
)
from .profiles import merge_with_protocol


def _product(pid: str, name: str, brand: str, unit: str, qty: float, *, claims: Optional[dict] = None,
             reliability: Optional[float] = None, category: str = "10400") -> ProductRecord:
    return ProductRecord(
        id=pid, name_en=name, brand=brand, vendor=brand, category=category, sales_unit=unit, lot_size=1,
        qty_per_sales_unit=qty, qty_unit="L", qty_source="UNIT_CONVERSIONS",
        claims=claims if claims is not None else {"FREE_FROM_LACTOSE": "STRUCTURED"},
        allergens={"AM": ("CONTAINS", "A")}, labels=[], flags=[], substitutions=[], deposits=[],
        delivery_reliability=reliability, co2_kg_per_kg=1.2, has_synkka=True,
    )


PRODUCTS: dict[str, ProductRecord] = {p.id: p for p in [
    _product("463038", "Valio Eila® 10 l novobox lactose free", "Valio", "KI", 20),
    _product("464306", "Valio Hyvä suomalainen Arki® Eila® 1 l lactose free", "Valio", "ST", 1, reliability=0.99),
    _product("464201", "Valio semi skimmed milk 1 l lactose free UHT", "Valio", "ST", 1, reliability=0.99),
    _product("464179", "Arla 10 L Novobox lactose free milk drink ESL", "Arla", "ST", 10, reliability=0.95),
    _product("999001", "Farm milk 1 l lactose free", "Tila", "ST", 1, claims={"FREE_FROM_LACTOSE": "NAME_ONLY"},
             reliability=0.85),
    _product("999002", "Whole milk 1 l", "Valio", "ST", 1, claims={}, reliability=0.99),
    _product("469869", "Filos Greek cheese lactose free", "Filos", "ST", 1, category="10600"),
]}

PRICES_CENTS = {"463038": 1760, "464306": 85, "464201": 95, "464179": 900, "999001": 80, "999002": 70}


class StubCatalog:
    def get(self, product_id: str) -> ProductRecord:
        return PRODUCTS[product_id]

    def filter(self, req: StructuredRequest, rules: EffectiveRules) -> FilterResult:
        kept, blocked, dropped = [], [], 0
        for p in PRODUCTS.values():
            if p.category != req.category:
                dropped += 1
                continue
            claims_needed = [r.code for r in rules.hard_requirements + req.requirements if r.kind == "CLAIM"]
            if any(c not in p.claims for c in claims_needed):
                dropped += 1
                continue
            violations = []
            if p.brand in rules.excluded_brands:
                violations.append(Violation(rule="PROFILE_EXCLUDED_BRAND", detail=f"Brand {p.brand} is excluded in your profile", product_id=p.id))
            if p.id in rules.excluded_product_ids:
                violations.append(Violation(rule="PROFILE_EXCLUDED_PRODUCT", detail="Product is excluded", product_id=p.id))
            (blocked.append((p, violations)) if violations else kept.append(p))
        event = ChainEvent(at=fx.NOW, title="Filtered the catalogue", logic="Used manufacturer structured claims only, not product names",
                           evidence=[EvidenceRef(source="CATALOG", detail=f"Category {req.category} + {', '.join(r.code for r in rules.hard_requirements)}", trust_grade="A")])
        return FilterResult(kept=kept, blocked=blocked, dropped_count=dropped, events=[event])

    def official_substitutes(self, product_id: str) -> list[ProductRecord]:
        return []

    def same_category_alternatives(self, product: ProductRecord, rules: EffectiveRules, limit: int = 5) -> list[ProductRecord]:
        return []


class StubUnits:
    def convert(self, product: ProductRecord, qty: Quantity) -> Conversion:
        if not product.qty_per_sales_unit or product.qty_unit != qty.unit:
            return Conversion(sales_units=0, qty_covered=qty, overbuy=None, source="UNKNOWN", exact=False)
        units = math.ceil(qty.value / product.qty_per_sales_unit / product.lot_size) * product.lot_size
        covered = units * product.qty_per_sales_unit
        overbuy = Quantity(value=covered - qty.value, unit=qty.unit) if covered > qty.value else None
        return Conversion(sales_units=units, qty_covered=Quantity(value=covered, unit=qty.unit), overbuy=overbuy,
                          source="UNIT_CONVERSIONS", exact=True)


class StubValio:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []   # lets tests check batching

    def prices(self, product_ids: list[str], buyer_id: str, *,
               delivery_date: Optional[date] = None) -> dict[str, PriceInfo]:
        self.calls.append(("prices", list(product_ids)))
        return {pid: PriceInfo(unit_price_cents=PRICES_CENTS[pid], vat_rate=0.135, deposit_cents_per_unit=0)
                for pid in product_ids if pid in PRICES_CENTS}

    def availability(self, product_ids: list[str], delivery_date: date, sales_units: dict[str, float],
                     agent_id: str) -> dict[str, AvailabilityInfo]:
        self.calls.append(("availability", list(product_ids)))
        return {pid: AvailabilityInfo(status="YES", deliverable_sales_units=None) for pid in product_ids}


_LEVELS: list[Level] = ["LOW", "MEDIUM", "HIGH"]


class StubCards:
    def allergen_card(self, lines: list[CandidateLine], rules: EffectiveRules) -> AllergenCard:
        rows = []
        for line in lines:
            p = PRODUCTS[line.product_id]
            for r in rules.hard_requirements:
                src = p.claims.get(r.code)
                rows.append(AllergenRow(product_id=p.id, requirement=r, status="CLAIMED" if src else "NOT_CLAIMED",
                                        trust_grade="A" if src == "STRUCTURED" else "C" if src else "D",
                                        source="Manufacturer structured claim" if src == "STRUCTURED" else "Product name only"))
        return AllergenCard(rows=rows)

    def trust_score(self, lines, req, rules, gate_preview) -> TrustScore:
        card = self.allergen_card(lines, rules)
        evidence: Level = "HIGH" if all(r.trust_grade == "A" for r in card.rows) else "LOW"
        rel = [PRODUCTS[l.product_id].delivery_reliability for l in lines]
        fulfilment: Level = "MEDIUM" if None in rel else "HIGH" if min(rel) >= 0.98 else "MEDIUM" if min(rel) >= 0.9 else "LOW"
        compliance: Level = {"AUTO": "HIGH", "BUYER_APPROVAL": "MEDIUM"}.get(gate_preview.outcome, "LOW")
        dims = [evidence, "HIGH", fulfilment, compliance]
        levels = {"evidence": evidence, "match": "HIGH", "fulfilment": fulfilment, "compliance": compliance}
        evidence_checks = [("HIGH" if r.trust_grade == "A" else "LOW") for r in card.rows]
        return TrustScore(evidence=evidence, match="HIGH", fulfilment=fulfilment, compliance=compliance,
                          overall=min(dims, key=_LEVELS.index), points=trust_points({"evidence": evidence_checks}, levels))

    def build(self, lines, req, rules, gate_preview) -> Cards:
        return Cards(allergen=self.allergen_card(lines, rules), trust=self.trust_score(lines, req, rules, gate_preview),
                     tips=[Tip(kind="QUOTE_TIMER", text="Quote is valid for 15 minutes after you select it")])


class StubDisclosure:
    def filter(self, obj: dict, audience: Audience) -> dict:
        return obj

    def record(self, audience, request_id, fields) -> None:
        pass

    def pack_for_llm(self, obj: dict, audience: Audience) -> DisclosablePack:
        return DisclosablePack(data={k: v for k, v in obj.items() if k != "marketing_text_en"}, max_sensitivity="L1")


class StubProtocols:
    def get_active(self, protocol_id: str, agent_id: Optional[str]) -> Protocol:
        return fx.PROTOCOL

    def effective_rules(self, protocol: Protocol, profile: Optional[Profile]) -> EffectiveRules:
        return merge_with_protocol(protocol.body, profile.body if profile else None)


class StubAudit:
    """In-memory AuditApi with the signature agreed in the holds-report spec (buyer_id, agent_id,
    reason_code added). No hash chain: BE1 owns that. Replace with BE1's AuditApi when it lands."""

    def __init__(self) -> None:
        self.entries: list[dict] = []

    def append(self, event: str, actor: str, payload: dict, request_id: Optional[str] = None,
               visibility: str = "BOTH", summary: str = "", buyer_id: Optional[str] = None,
               agent_id: Optional[str] = None, reason_code: Optional[str] = None) -> int:
        self.entries.append(dict(seq=len(self.entries) + 1, event=event, actor=actor, payload=payload,
                                 request_id=request_id, visibility=visibility, summary=summary,
                                 buyer_id=buyer_id, agent_id=agent_id, reason_code=reason_code))
        return len(self.entries)

    def of(self, event: str) -> list[dict]:
        return [e for e in self.entries if e["event"] == event]


_OUTCOME_ORDER = ["AUTO", "BUYER_APPROVAL", "VALIO_REVIEW", "REJECT"]


class StubGate:
    def preview(self, lines: list[CandidateLine], totals_gross_cents: int, rules: EffectiveRules,
                data_unverified: bool) -> GateDecision:
        hits = []
        if totals_gross_cents > rules.max_cents:
            hits.append(RuleHit(rule_id="R-SPEND-MAX", description="Total above the order maximum", outcome="REJECT"))
        elif totals_gross_cents > rules.auto_limit_cents:
            hits.append(RuleHit(rule_id="R-SPEND-APPROVAL", description="Total above the auto-approve limit", outcome="BUYER_APPROVAL"))
        if data_unverified:
            hits.append(RuleHit(rule_id="R-DATA-UNVERIFIED", description="A hard requirement is not verified", outcome="BUYER_APPROVAL"))
        if any(l.available_on_date == "PARTIAL" for l in lines):
            hits.append(RuleHit(rule_id="R-DELIVERY-PARTIAL", description="Partly deliverable", outcome="BUYER_APPROVAL"))
        if not hits:
            hits.append(RuleHit(rule_id="R-SPEND-AUTO", description="Total within the auto-approve limit", outcome="AUTO"))
        outcome = max((h.outcome for h in hits), key=_OUTCOME_ORDER.index)
        return GateDecision(outcome=outcome, rules_hit=hits, summary="; ".join(h.description for h in hits))

    def evaluate(self, quote, rules) -> GateDecision:
        raise NotImplementedError("BE1 owns the real gate")
