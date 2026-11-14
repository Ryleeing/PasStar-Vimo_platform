"""Build BE2 fact cards from catalogue evidence and a supplied gate preview.

No network calls or gate decisions happen here.  Raw supplier reliability is
kept internal; a caller must explicitly configure reviewed thresholds before
it can be described as HIGH/MEDIUM/LOW.
"""
from __future__ import annotations

import math
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from ..internal import CatalogApi, EffectiveRules, ProductRecord
from ..schemas import (
    AllergenCard,
    AllergenRow,
    CandidateLine,
    Cards,
    GateDecision,
    Requirement,
    StructuredRequest,
    Tip,
    TrustScore,
)
from .repository import NON_CERTIFICATION_LABELS, requirement_check
from .trust import FRESH_DAYS


ReliabilityLevel = Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]
_LEVEL_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
_GRADE_LEVEL = {"A": "HIGH", "B": "MEDIUM", "C": "LOW", "D": "LOW"}


def _minimum(levels: list[str]) -> str:
    return min(levels, key=_LEVEL_RANK.__getitem__) if levels else "LOW"


def _requirements(*groups: list[Requirement]) -> list[Requirement]:
    result: dict[tuple[str, str], Requirement] = {}
    for group in groups:
        for requirement in group:
            result.setdefault((requirement.kind, requirement.code), requirement)
    return list(result.values())


def _finite(value: object, *, minimum: float = 0) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= minimum
    )


def _timestamp(value: datetime) -> datetime:
    # Original Synkka timestamps contain offsets.  Treat a legacy naive value
    # as UTC solely for comparing evidence ages, without inventing a new date.
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class CardsBuilder:
    """Assemble deterministic cards without inventing missing product facts."""

    def __init__(
        self,
        catalog: CatalogApi,
        *,
        today: date | None = None,
        reliability_high: float | None = None,
        reliability_medium: float | None = None,
    ) -> None:
        if (reliability_high is None) != (reliability_medium is None):
            raise ValueError("both reliability thresholds must be provided together")
        if reliability_high is not None and (
            not _finite(reliability_high)
            or not _finite(reliability_medium)
            or not 0 <= reliability_medium <= reliability_high <= 1
        ):
            raise ValueError("reliability thresholds must satisfy 0 <= medium <= high <= 1")
        self.catalog = catalog
        self.today = today or datetime.now(ZoneInfo("Europe/Helsinki")).date()
        self.reliability_high = reliability_high
        self.reliability_medium = reliability_medium

    def reliability_level(
        self, product: ProductRecord | list[ProductRecord]
    ) -> ReliabilityLevel:
        """Expose only a configured level; any missing value makes it UNKNOWN."""
        products = product if isinstance(product, list) else [product]
        if not products or self.reliability_high is None:
            return "UNKNOWN"
        levels: list[str] = []
        for record in products:
            value = record.delivery_reliability
            if not _finite(value) or value > 1:
                return "UNKNOWN"
            if value >= self.reliability_high:
                levels.append("HIGH")
            elif value >= self.reliability_medium:
                levels.append("MEDIUM")
            else:
                levels.append("LOW")
        return _minimum(levels)

    def _records(self, lines: list[CandidateLine]) -> dict[str, ProductRecord]:
        return {line.product_id: self.catalog.get(line.product_id) for line in lines}

    def allergen_card(
        self, lines: list[CandidateLine], rules: EffectiveRules
    ) -> AllergenCard:
        records = self._records(lines)
        requirements = _requirements(
            rules.protocol.hard_requirements, rules.hard_requirements
        )
        rows: list[AllergenRow] = []
        notes: list[str] = []
        for record in records.values():
            for requirement in requirements:
                if requirement.kind == "LABEL":
                    continue
                checked = requirement_check(record, requirement, self.today)
                rows.append(
                    AllergenRow(
                        product_id=record.id,
                        requirement=requirement,
                        status=checked.status,
                        trust_grade=checked.grade,
                        source=checked.source,
                    )
                )
                if checked.verdict == "UNVERIFIED":
                    note = "Undeclared or inferred data does not establish allergen safety."
                    if note not in notes:
                        notes.append(note)
                if checked.conflict:
                    note = "Conflicting manufacturer or name evidence requires review."
                    if note not in notes:
                        notes.append(note)

        if any(requirement.code == "FREE_FROM_LACTOSE" for requirement in requirements):
            notes.append("Lactose-free does not mean milk-free.")

        dates = [getattr(record, "synkka_updated_at", None) for record in records.values()]
        updated_at = None
        if dates and all(isinstance(value, datetime) for value in dates):
            updated_at = min(dates, key=_timestamp)
        return AllergenCard(rows=rows, data_updated_at=updated_at, notes=notes)

    def trust_score(
        self,
        lines: list[CandidateLine],
        req: StructuredRequest,
        rules: EffectiveRules,
        gate_preview: GateDecision,
    ) -> TrustScore:
        records = self._records(lines)
        requirements = _requirements(
            rules.protocol.hard_requirements, rules.hard_requirements, req.requirements
        )
        checks = [
            requirement_check(record, requirement, self.today)
            for record in records.values()
            for requirement in requirements
        ]
        improvements: list[str] = []

        evidence_levels: list[str] = []
        for record in records.values():
            updated = getattr(record, "synkka_updated_at", None)
            if not record.has_synkka or not isinstance(updated, datetime):
                evidence_levels.append("LOW")
            else:
                age = (self.today - updated.date()).days
                evidence_levels.append(
                    "LOW" if age < 0 else "HIGH" if age <= FRESH_DAYS else "MEDIUM"
                )
            if any("CONFLICT" in flag for flag in record.flags):
                evidence_levels.append("LOW")
        evidence_levels.extend(_GRADE_LEVEL[checked.grade] for checked in checks)
        if any(line.conversion_source != "UNIT_CONVERSIONS" for line in lines):
            evidence_levels.append("LOW")
            improvements.append("Confirm the quantity per sales unit with the manufacturer.")
        evidence = _minimum(evidence_levels)
        if evidence != "HIGH":
            improvements.append("Obtain current, consistent manufacturer declarations.")

        match_levels = ["HIGH"] if lines else ["LOW"]
        for checked in checks:
            if checked.verdict in {"VIOLATED", "NOT_MATCHED"}:
                match_levels.append("LOW")
            elif checked.verdict == "UNVERIFIED":
                match_levels.append("MEDIUM" if checked.grade == "B" else "LOW")
        if any(record.category != req.category for record in records.values()):
            match_levels.append("MEDIUM")
        if any(
            line.conversion_source == "UNKNOWN"
            or line.qty_covered.unit != req.quantity.unit
            or not _finite(line.sales_units, minimum=0.000000001)
            for line in lines
        ):
            match_levels.append("LOW")
        elif any(line.conversion_source == "NAME_PARSE" for line in lines):
            match_levels.append("MEDIUM")
        if lines and all(line.qty_covered.unit == req.quantity.unit for line in lines):
            if sum(line.qty_covered.value for line in lines) < req.quantity.value - 1e-9:
                match_levels.append("LOW")
        match = _minimum(match_levels)
        if any(checked.verdict != "SATISFIED" for checked in checks):
            improvements.append("Verify every hard requirement before ordering.")

        all_prices = bool(lines) and all(
            _finite(line.unit_price_cents)
            and _finite(line.line_net_cents)
            and _finite(line.vat_rate)
            and line.vat_rate <= 1
            for line in lines
        )
        if not lines or any(line.available_on_date in {"UNKNOWN", "NO"} for line in lines):
            fulfilment = "LOW"
        elif not all_prices:
            fulfilment = "LOW"
        elif any(line.available_on_date == "PARTIAL" for line in lines):
            fulfilment = "MEDIUM"
        else:
            reliability = self.reliability_level(list(records.values()))
            fulfilment = "HIGH" if reliability == "HIGH" else "MEDIUM"
            if reliability == "LOW":
                fulfilment = "LOW"
        if fulfilment != "HIGH":
            improvements.append("Confirm price, deliverability and delivery reliability.")

        compliance = {
            "AUTO": "HIGH",
            "BUYER_APPROVAL": "MEDIUM",
            "VALIO_REVIEW": "MEDIUM",
            "REJECT": "LOW",
        }[gate_preview.outcome]
        if (
            not lines
            or rules.protocol.valid_until < self.today
            or req.category not in rules.protocol.allowed_categories
            or any(checked.verdict == "VIOLATED" for checked in checks)
            or any(
                record.category not in rules.protocol.allowed_categories
                for record in records.values()
            )
            or any(record.id in rules.excluded_product_ids for record in records.values())
            or any(
                (record.brand or "").casefold()
                in {brand.casefold() for brand in rules.excluded_brands}
                or record.vendor.casefold()
                in {vendor.casefold() for vendor in rules.excluded_vendors}
                for record in records.values()
            )
        ):
            compliance = "LOW"
        if compliance != "HIGH":
            improvements.append("Resolve the supplied protocol gate decision.")

        overall = _minimum([evidence, match, fulfilment, compliance])
        return TrustScore(
            evidence=evidence,
            match=match,
            fulfilment=fulfilment,
            compliance=compliance,
            overall=overall,
            how_to_improve=" ".join(dict.fromkeys(improvements)) or None,
        )

    def build(
        self,
        lines: list[CandidateLine],
        req: StructuredRequest,
        rules: EffectiveRules,
        gate_preview: GateDecision,
    ) -> Cards:
        card_rules = replace(
            rules,
            hard_requirements=_requirements(rules.hard_requirements, req.requirements),
        )
        tips: list[Tip] = []
        records = self._records(lines)
        for line in lines:
            record = records[line.product_id]
            prefix = f"{record.id}: "
            if line.overbuy is not None:
                tips.append(
                    Tip(
                        kind="ROUNDING",
                        text=(
                            f"{prefix}rounded to the sales lot; extra quantity "
                            f"{line.overbuy.value:g} {line.overbuy.unit}."
                        ),
                    )
                )
            if line.deposit_cents > 0:
                tips.append(
                    Tip(kind="DEPOSIT", text=f"{prefix}deposit charge {line.deposit_cents} cents.")
                )
            elif record.deposits:
                tips.append(
                    Tip(
                        kind="DEPOSIT",
                        text=f"{prefix}deposit packaging is listed; confirm its charge.",
                    )
                )
            minimum = getattr(record, "min_temp_c", None)
            maximum = getattr(record, "max_temp_c", None)
            if _finite(minimum, minimum=-273.15) and _finite(maximum, minimum=-273.15):
                text = f"Store between {minimum:g} and {maximum:g} °C."
            elif _finite(maximum, minimum=-273.15):
                text = f"Store at or below {maximum:g} °C."
            elif _finite(minimum, minimum=-273.15):
                text = f"Store at or above {minimum:g} °C."
            else:
                text = None
            if text:
                tips.append(Tip(kind="STORAGE", text=prefix + text))
            volume = getattr(record, "case_volume_l", None)
            if _finite(volume, minimum=0.000000001):
                tips.append(
                    Tip(
                        kind="COLD_STORAGE_SPACE",
                        text=f"{prefix}outer-case geometric volume {volume:g} L per case.",
                    )
                )
            level = self.reliability_level(record)
            tips.append(Tip(kind="RELIABILITY", text=f"{prefix}delivery reliability: {level}."))
            carbon = record.co2_kg_per_kg
            if _finite(carbon):
                tips.append(
                    Tip(
                        kind="CARBON",
                        text=(
                            f"{prefix}category-average footprint factor: {carbon:g} "
                            "kg CO2-eq per kg; this is not the order's total footprint."
                        ),
                    )
                )
            labels = sorted(set(record.labels) - NON_CERTIFICATION_LABELS)
            if labels:
                tips.append(
                    Tip(
                        kind="LABEL",
                        text=prefix + "Recorded label codes: " + ", ".join(labels),
                    )
                )

        return Cards(
            allergen=self.allergen_card(lines, card_rules),
            trust=self.trust_score(lines, req, rules, gate_preview),
            alternatives=[],
            tips=tips,
        )
