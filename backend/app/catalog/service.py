"""BE2's in-process catalogue pipeline, with explicit dynamic/gate dependencies.

Nothing here imports fixtures, calls an LLM, persists an order, or authorizes a
purchase.  ``generate`` returns structured candidates for the orchestrator.  A
Valio client and BE1 gate may be injected; absent dependencies produce unknown
stock/prices and a review preview.  Public HTTP routes are connected separately.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal
from zoneinfo import ZoneInfo

from ..internal import (
    Audience, AvailabilityInfo, CardsApi, CatalogApi, DisclosablePack, DisclosureApi,
    EffectiveRules, GateApi, PriceInfo, Conversion, ProductRecord, UnitsApi, ValioApi,
)
from ..schemas import (
    AllergenStatus, AlternativeRef, Candidate, CandidateLine, ChainEvent,
    GateDecision, ProductPublic, ProductSearchRequest, Profile, ProfileBody,
    Protocol, ProtocolBody, Requirement, RuleHit, StructuredRequest, Totals, Violation,
)
from .cards import CardsBuilder
from .disclosure import DisclosureFilter
from .repository import CatalogRepository, NON_CERTIFICATION_LABELS, requirement_check
from .units import UnitsConverter


HELSINKI = ZoneInfo("Europe/Helsinki")
_OUTCOME_ORDER = {"AUTO": 0, "BUYER_APPROVAL": 1, "VALIO_REVIEW": 2, "REJECT": 3}
_LEVEL_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def merged_requirements(*groups: list[Requirement]) -> list[Requirement]:
    """Union hard constraints by kind/code without mutating any input model."""
    found: dict[tuple[str, str], Requirement] = {}
    for group in groups:
        for requirement in group:
            found.setdefault(
                (requirement.kind, requirement.code), requirement.model_copy(deep=True)
            )
    return [found[key] for key in sorted(found)]


def effective_rules(
    protocol: Protocol | ProtocolBody, profile: Profile | ProfileBody | None = None,
) -> EffectiveRules:
    """Pure protocol/profile merge.  Profiles add restrictions and preferences.

    Protocol validation/signatures and profile persistence belong to BE1/BE3;
    this function only merges an already-validated body.  A buyer-owned Profile
    is checked against the protocol's buyer before its contents are used.
    """
    body = protocol.body if isinstance(protocol, Protocol) else protocol
    if isinstance(profile, Profile) and profile.buyer_id != body.buyer_id:
        raise ValueError("profile buyer does not match protocol buyer")
    preferences = profile.body if isinstance(profile, Profile) else profile
    if body.auto_approve_limit_cents < 0 or body.max_order_cents < 0:
        raise ValueError("protocol spend limits must be non-negative")
    if body.auto_approve_limit_cents > body.max_order_cents:
        raise ValueError("auto approval limit exceeds order maximum")

    exclusions = preferences.hard_exclusions if preferences else None
    weights = (
        preferences.weights.model_dump()
        if preferences else {"price": 0.4, "fulfilment": 0.4, "carbon": 0.1, "local": 0.1}
    )
    _normalised_weights(weights)
    return EffectiveRules(
        protocol=body.model_copy(deep=True),
        hard_requirements=merged_requirements(
            body.hard_requirements, exclusions.requirements if exclusions else []
        ),
        excluded_brands=list(exclusions.brands) if exclusions else [],
        excluded_vendors=list(exclusions.vendors) if exclusions else [],
        excluded_product_ids=list(exclusions.product_ids) if exclusions else [],
        auto_limit_cents=body.auto_approve_limit_cents,
        max_cents=body.max_order_cents,
        weights=dict(weights),
        soft=preferences.soft_preferences.model_dump() if preferences else {},
    )


def _normalised_weights(weights: dict[str, float]) -> dict[str, float]:
    keys = {"price", "fulfilment", "carbon", "local"}
    if set(weights) != keys:
        raise ValueError("ranking weights must contain price, fulfilment, carbon, local")
    if any(not math.isfinite(value) or value < 0 for value in weights.values()):
        raise ValueError("ranking weights must be finite and non-negative")
    total = sum(weights.values())
    if total <= 0 or not math.isfinite(total):
        raise ValueError("ranking weights must have a finite positive sum")
    return {key: value / total for key, value in weights.items()}


@dataclass(frozen=True)
class RankingPolicy:
    """Weighted scoring is opt-in until the business formula is frozen.

    Weighted mode uses min/max price and carbon scores in the <=8 shortlist,
    fulfilment YES/PARTIAL/NO = 1/.5/0, preferred-country match = 1, and a .5
    neutral score for missing facts.  Confidence breaks score ties first.
    """
    mode: Literal["conservative", "weighted"] = "conservative"

    def __post_init__(self) -> None:
        if self.mode not in {"conservative", "weighted"}:
            raise ValueError("unsupported ranking mode")


@dataclass
class CatalogResult:
    candidates: list[Candidate]
    blocked: list[Candidate]
    events: list[ChainEvent]
    dropped_count: int
    unconvertible_product_ids: list[str] = field(default_factory=list)
    unconverted_blocked: dict[str, list[Violation]] = field(default_factory=dict)
    no_candidate_reason: str | None = None
    request_id: str | None = None


class CatalogService:
    def __init__(
        self, catalog: CatalogApi | None = None, *, units: UnitsApi | None = None,
        cards: CardsApi | None = None, valio: ValioApi | None = None,
        gate: GateApi | None = None, today: date | None = None,
        ranking: RankingPolicy | None = None, disclosure: DisclosureApi | None = None,
    ) -> None:
        self.today = today or datetime.now(HELSINKI).date()
        self.catalog = catalog if catalog is not None else CatalogRepository(today=self.today)
        self.units = units if units is not None else UnitsConverter()
        self.cards = cards if cards is not None else CardsBuilder(self.catalog, today=self.today)
        self.valio = valio
        self.gate = gate
        self.ranking = ranking or RankingPolicy()
        self._disclosure = disclosure

    def _disclosure_filter(self) -> DisclosureApi:
        if self._disclosure is None:
            self._disclosure = DisclosureFilter()
        return self._disclosure

    def get_product(
        self, product_id: str, *, audience: Audience | None = None,
    ) -> ProductPublic:
        return self._public_response(self.catalog.get(product_id), audience)

    def search(
        self, request: ProductSearchRequest, *, audience: Audience | None = None,
    ) -> list[ProductPublic]:
        return [self._public_response(product, audience) for product in self.catalog.search(request)]

    def _public_response(
        self, product: ProductRecord, audience: Audience | None,
    ) -> ProductPublic:
        public = self._public(product)
        filtered = self._disclosure_filter().filter(
            {"product": public.model_dump(mode="json")},
            audience or Audience("BUYER_USER", None, None, "L0"),
        )
        return ProductPublic.model_validate(filtered.get("product", {}))

    def pack_for_llm(
        self, result: CatalogResult, req: StructuredRequest, audience: Audience,
    ) -> DisclosablePack:
        """Export only policy-approved facts; generate() remains an internal result.

        The caller must verify buyer/protocol ownership before constructing the
        audience. Free text in the returned data must remain data in LLM prompts.
        """
        return self._disclosure_filter().pack_for_llm({
            "request": req.model_dump(mode="json"),
            "candidates": [item.model_dump(mode="json") for item in result.candidates],
            "blocked": [item.model_dump(mode="json") for item in result.blocked],
            "events": [item.model_dump(mode="json") for item in result.events],
            "dropped_count": result.dropped_count,
            "unconvertible_product_ids": list(result.unconvertible_product_ids),
            "no_candidate_reason": result.no_candidate_reason,
        }, audience, request_id=result.request_id)

    def _public(self, product: ProductRecord) -> ProductPublic:
        flags = set(product.flags)
        if product.qty_source == "NAME_PARSE":
            flags.add("QUANTITY_NAME_INFERRED")
        return ProductPublic(
            id=product.id, name=product.name_en, brand=product.brand,
            category=product.category, sales_unit=product.sales_unit,
            qty_per_sales_unit=product.qty_per_sales_unit, qty_unit=product.qty_unit,
            claims=sorted(
                code for code, source in product.claims.items() if source == "STRUCTURED"
            ),
            labels=sorted(set(product.labels) - NON_CERTIFICATION_LABELS),
            allergens=[
                AllergenStatus(
                    code=code, label=code,
                    status=requirement_check(
                        product, Requirement(kind="ALLERGEN_FREE", code=code), self.today
                    ).status,
                    trust_grade=requirement_check(
                        product, Requirement(kind="ALLERGEN_FREE", code=code), self.today
                    ).grade,
                )
                for code in sorted(product.allergens)
            ],
            country=product.country, has_manufacturer_data=product.has_synkka,
            data_updated_at=product.synkka_updated_at, flags=sorted(flags),
        )

    def generate(
        self, req: StructuredRequest, rules: EffectiveRules, *,
        request_id: str = "request", limit: int = 8,
    ) -> CatalogResult:
        """Generate single-SKU options; query dynamic data for at most 8 SKUs.

        Requests for which no verified physical conversion exists are reported
        explicitly instead of inventing CandidateLine.qty_covered.  Name-derived
        conversions can form options, but always require review.
        """
        if not 1 <= limit <= 8:
            raise ValueError("candidate limit must be between 1 and 8")
        if req.budget_cents is not None and req.budget_cents < 0:
            raise ValueError("request budget must be non-negative")
        combined = merged_requirements(
            rules.protocol.hard_requirements, rules.hard_requirements, req.requirements
        )
        # Reconstruct the effective rules to prevent mutable caller lists from
        # dropping protocol constraints.  Spending limits can only tighten.
        working = EffectiveRules(
            protocol=rules.protocol.model_copy(deep=True), hard_requirements=combined,
            excluded_brands=list(rules.excluded_brands),
            excluded_vendors=list(rules.excluded_vendors),
            excluded_product_ids=list(rules.excluded_product_ids),
            auto_limit_cents=min(rules.auto_limit_cents, rules.protocol.auto_approve_limit_cents),
            max_cents=min(rules.max_cents, rules.protocol.max_order_cents),
            weights=dict(rules.weights), soft=dict(rules.soft),
        )
        if working.max_cents < 0 or working.auto_limit_cents < 0:
            raise ValueError("effective spend limits must be non-negative")
        if self.ranking.mode == "weighted":
            _normalised_weights(working.weights)
        filtered = self.catalog.filter(req, working)
        conversions = {}
        unconvertible = []
        for product in filtered.kept:
            conversion = self.units.convert(product, req.quantity)
            if conversion.source == "UNKNOWN" or conversion.qty_covered is None:
                unconvertible.append(product.id)
            else:
                conversions[product.id] = conversion

        shortlist = sorted(
            (product for product in filtered.kept if product.id in conversions),
            key=lambda product: self._static_key(product, working),
        )[:limit]
        ids = [product.id for product in shortlist]
        prices = {}
        availability = {}
        if self.valio is not None and ids:
            # Upstream errors deliberately propagate to BE3; retries and error
            # translation belong to the injected client/orchestrator.
            prices = self.valio.prices(
                ids, working.protocol.buyer_id, delivery_date=req.delivery_date
            )
            availability = self.valio.availability(
                ids, req.delivery_date,
                {product_id: conversions[product_id].sales_units for product_id in ids},
                working.protocol.agent_id,
            )

        candidates = []
        by_id = {product.id: product for product in shortlist}
        for product in shortlist:
            conversion = conversions[product.id]
            info = availability.get(product.id, AvailabilityInfo("UNKNOWN", None))
            line, totals = self._line(
                product, conversion, prices.get(product.id), info
            )
            checks = [requirement_check(product, item, self.today) for item in combined]
            unverified = any(item.verdict != "SATISFIED" for item in checks)
            preview = self._preview(line, totals, working, req, unverified)
            cards = self.cards.build([line], req, working, preview)
            candidates.append(Candidate(
                id=self._candidate_id(request_id, product.id),
                selectable=preview.outcome != "REJECT", lines=[line], totals=totals,
                delivery_date=req.delivery_date,
                reliability_level=self._reliability(product),
                outcome_preview=preview.outcome,
                outcome_reasons=[hit.rule_id for hit in preview.rules_hit],
                cards=cards,
                violations=[
                    Violation(rule=hit.rule_id, detail=hit.description, product_id=product.id)
                    for hit in preview.rules_hit if hit.outcome == "REJECT"
                ],
                explanation="Catalogue facts assembled; " + preview.summary,
            ))

        selectable = [candidate for candidate in candidates if candidate.selectable]
        ranked = sorted(
            selectable,
            key=lambda candidate: self._rank_key(candidate, by_id, working, selectable),
        )
        for rank, candidate in enumerate(ranked, start=1):
            candidate.rank = rank
        if ranked:
            ranked[0].badge = "RECOMMENDED"
        for candidate in ranked:
            candidate.cards.alternatives = [
                AlternativeRef(
                    candidate_id=other.id, tier="SAME_CATEGORY",
                    tradeoff="Same-category option; compare price, delivery and evidence cards.",
                )
                for other in ranked if other.id != candidate.id
            ] if req.substitution != "NONE" and (
                working.protocol.substitution.same_category != "FORBIDDEN"
            ) else []

        blocked = [candidate for candidate in candidates if not candidate.selectable]
        unconverted_blocked = {}
        for product, violations in filtered.blocked:
            conversion = self.units.convert(product, req.quantity)
            # Preserve violations even when the public line model cannot
            # truthfully represent a quantity: a blocked option can have no lines.
            if conversion.qty_covered is None or conversion.source == "UNKNOWN":
                unconvertible.append(product.id)
                unconverted_blocked[product.id] = violations
                preview = GateDecision(
                    outcome="REJECT", rules_hit=[], summary="Hard constraint violated"
                )
                blocked.append(Candidate(
                    id=self._candidate_id(request_id, product.id), selectable=False, lines=[],
                    delivery_date=req.delivery_date, reliability_level="UNKNOWN",
                    outcome_preview="REJECT", outcome_reasons=[item.rule for item in violations],
                    cards=self.cards.build([], req, working, preview), violations=violations,
                    explanation="Hard constraint violated; quantity conversion is unavailable.",
                ))
                continue
            line, _ = self._line(product, conversion, None, AvailabilityInfo("UNKNOWN", None))
            preview = GateDecision(outcome="REJECT", rules_hit=[], summary="Hard constraint violated")
            blocked.append(Candidate(
                id=self._candidate_id(request_id, product.id), selectable=False,
                lines=[line], delivery_date=req.delivery_date, reliability_level="UNKNOWN",
                outcome_preview="REJECT", outcome_reasons=[item.rule for item in violations],
                cards=self.cards.build([line], req, working, preview), violations=violations,
                explanation="Hard constraint violated; this product cannot be selected.",
            ))

        return CatalogResult(
            candidates=ranked, blocked=blocked, events=filtered.events,
            dropped_count=filtered.dropped_count,
            unconvertible_product_ids=sorted(set(unconvertible)),
            unconverted_blocked=unconverted_blocked,
            no_candidate_reason=(
                None if ranked else (
                    "No selectable product has a supported quantity conversion and policy preview."
                )
            ),
            request_id=request_id,
        )

    @staticmethod
    def _candidate_id(request_id: str, product_id: str) -> str:
        digest = hashlib.sha256(f"{request_id}\0{product_id}".encode()).hexdigest()[:20]
        return f"cand_{digest}"

    def _static_key(self, product: ProductRecord, rules: EffectiveRules) -> tuple:
        grades = [
            requirement_check(product, requirement, self.today).grade
            for requirement in rules.hard_requirements
        ]
        confidence = max(({"A": 0, "B": 1, "C": 2, "D": 3}[g] for g in grades), default=0)
        country_match = product.country in rules.soft.get("countries", [])
        label_match = len(set(product.labels) & set(rules.soft.get("labels", [])))
        large = (
            (product.qty_per_sales_unit or 0) if rules.soft.get("prefer_large_packs") else 0
        )
        return (
            confidence, product.qty_source != "UNIT_CONVERSIONS", -country_match,
            -label_match, -large, product.id,
        )

    def _reliability(self, product: ProductRecord) -> str:
        method = getattr(self.cards, "reliability_level", None)
        return method(product) if method else "UNKNOWN"

    @staticmethod
    def _line(
        product: ProductRecord, conversion: Conversion,
        price: PriceInfo | None, info: AvailabilityInfo,
    ) -> tuple[CandidateLine, Totals | None]:
        if info.status not in {"YES", "PARTIAL", "NO", "UNKNOWN"}:
            raise ValueError("invalid availability status")
        deliverable = None
        if info.status == "PARTIAL" and info.deliverable_sales_units is not None:
            if not math.isfinite(info.deliverable_sales_units) or info.deliverable_sales_units < 0:
                raise ValueError("deliverable sales units must be finite and non-negative")
            deliverable = min(info.deliverable_sales_units, conversion.sales_units)
        totals = None
        unit_price = line_net = vat_rate = None
        deposit = 0
        if price is not None:
            if any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0
                for value in (price.unit_price_cents, price.deposit_cents_per_unit)
            ) or not math.isfinite(price.vat_rate) or not 0 <= price.vat_rate <= 1:
                raise ValueError("invalid price, deposit or VAT from price provider")
            if price.unit_gross_cents is not None and (
                not isinstance(price.unit_gross_cents, int)
                or isinstance(price.unit_gross_cents, bool)
                or price.unit_gross_cents < price.unit_price_cents
            ):
                raise ValueError("invalid tax-inclusive unit price from price provider")
            count = Decimal(str(conversion.sales_units))
            def cents(value: Decimal) -> int:
                return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            unit_price = price.unit_price_cents
            vat_rate = price.vat_rate
            line_net = cents(count * unit_price)
            deposit = cents(count * price.deposit_cents_per_unit)
            product_gross = (
                cents(count * price.unit_gross_cents)
                if price.unit_gross_cents is not None else
                line_net + cents(Decimal(line_net) * Decimal(str(vat_rate)))
            )
            vat = product_gross - line_net
            if not product.deposits or price.deposit_cents_per_unit > 0:
                totals = Totals(
                    net_cents=line_net, vat_cents=vat, deposit_cents=deposit,
                    gross_cents=product_gross + deposit,
                )
        line = CandidateLine(
            product_id=product.id, name=product.name_en, brand=product.brand,
            sales_unit=product.sales_unit, sales_units=conversion.sales_units,
            qty_covered=conversion.qty_covered, overbuy=conversion.overbuy,
            conversion_source=conversion.source, unit_price_cents=unit_price,
            line_net_cents=line_net, vat_rate=vat_rate, deposit_cents=deposit,
            available_on_date=info.status, deliverable_sales_units=deliverable,
        )
        return line, totals

    def _preview(
        self, line: CandidateLine, totals: Totals | None, rules: EffectiveRules,
        req: StructuredRequest, unverified: bool,
    ) -> GateDecision:
        hits = []
        if self.gate is not None and totals is not None:
            decision = self.gate.preview([line], totals.gross_cents, rules, unverified)
            hits.extend(decision.rules_hit)
            # Honour the outcome even if the external provider omitted hits.
            hits.append(RuleHit(
                rule_id="BE1-PREVIEW", description=decision.summary, outcome=decision.outcome
            ))
        else:
            hits.append(RuleHit(
                rule_id="R-PRICE-UNKNOWN" if totals is None else "R-GATE-UNAVAILABLE",
                description="A complete price and BE1 gate preview are required before ordering",
                outcome="VALIO_REVIEW" if totals is None else "BUYER_APPROVAL",
            ))

        def hit(rule, description, outcome):
            hits.append(RuleHit(rule_id=rule, description=description, outcome=outcome))

        if unverified:
            hit("R-DATA-UNVERIFIED", "Hard requirement evidence needs verification",
                "REJECT" if rules.protocol.unknown_data_policy == "REJECT" else "BUYER_APPROVAL")
        if rules.protocol.valid_until < self.today:
            hit("R-PROTOCOL-EXPIRED", "Protocol is expired", "REJECT")
        if line.conversion_source != "UNIT_CONVERSIONS":
            hit("R-QTY-INEXACT", "Quantity is inferred from the product name", "BUYER_APPROVAL")
        if line.available_on_date == "NO":
            hit("R-DELIVERY-NONE", "Product is not deliverable on the requested date", "REJECT")
        elif line.available_on_date in {"PARTIAL", "UNKNOWN"}:
            hit("R-DELIVERY-" + line.available_on_date,
                "Delivery is partial or unverified", "BUYER_APPROVAL")
        if totals is not None:
            maximum = (
                min(rules.max_cents, req.budget_cents)
                if req.budget_cents is not None else rules.max_cents
            )
            if totals.gross_cents > maximum:
                hit("R-SPEND-MAX", "Total exceeds the effective budget", "REJECT")
            elif totals.gross_cents > rules.auto_limit_cents:
                hit("R-SPEND-APPROVAL", "Total exceeds the automatic approval limit", "BUYER_APPROVAL")
        outcome = max((item.outcome for item in hits), key=_OUTCOME_ORDER.__getitem__)
        return GateDecision(outcome=outcome, rules_hit=hits, summary={
            "AUTO": "BE1 preview permits automatic processing",
            "BUYER_APPROVAL": "Buyer approval is required",
            "VALIO_REVIEW": "Valio review is required for a complete quote",
            "REJECT": "The option violates a hard constraint",
        }[outcome])

    def _rank_key(self, candidate, products, rules, all_candidates):
        product = products[candidate.lines[0].product_id]
        confidence = -_LEVEL_ORDER[candidate.cards.trust.overall]
        availability = {"YES": 0, "PARTIAL": 1, "UNKNOWN": 2, "NO": 3}[
            candidate.lines[0].available_on_date
        ]
        price = candidate.totals.gross_cents if candidate.totals else math.inf
        stable = confidence, availability, price, self._static_key(product, rules), product.id
        if self.ranking.mode == "conservative":
            return stable

        def lower_better(value, values):
            known = [item for item in values if item is not None]
            if value is None or not known:
                return 0.5
            low, high = min(known), max(known)
            return 0.5 if low == high else (high - value) / (high - low)

        weights = _normalised_weights(rules.weights)
        price_score = lower_better(
            candidate.totals.gross_cents if candidate.totals else None,
            [item.totals.gross_cents if item.totals else None for item in all_candidates],
        )
        carbon_score = lower_better(
            product.co2_kg_per_kg,
            [products[item.lines[0].product_id].co2_kg_per_kg for item in all_candidates],
        )
        fulfilment = {"YES": 1.0, "PARTIAL": 0.5, "UNKNOWN": 0.5, "NO": 0.0}[
            candidate.lines[0].available_on_date
        ]
        countries = rules.soft.get("countries", [])
        local = float(product.country in countries) if product.country and countries else 0.5
        score = sum(weights[key] * value for key, value in {
            "price": price_score, "fulfilment": fulfilment,
            "carbon": carbon_score, "local": local,
        }.items())
        return (-score, *stable)
