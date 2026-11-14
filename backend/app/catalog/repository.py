"""Read-only catalogue access, structured filtering, and evidence checks.

The catalogue is built offline by ``scripts/preprocess.py``.  Connections use
SQLite's read-only mode and are short-lived so an atomic catalogue replacement
is visible on the next operation.  No marketing text is read by this module.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import replace
from contextlib import closing
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from ..internal import EffectiveRules, FilterResult, ProductRecord
from ..schemas import (
    ChainEvent, EvidenceRef, ProductSearchRequest, Requirement, StructuredRequest, Violation,
)
from .trust import (
    CLAIM_TO_ALLERGEN, NAME_PATTERNS, ProductFacts, RequirementCheck,
    check_allergen_free, check_claim,
)


DEFAULT_DATABASE = Path(__file__).resolve().parents[2] / "data" / "products.sqlite"
NON_CERTIFICATION_LABELS = {"warmTemperature", "coolTemperature", "freezeTemperature"}
HELSINKI = ZoneInfo("Europe/Helsinki")


class ProductNotFoundError(KeyError):
    """The requested product is absent from the offline catalogue."""


def facts_from_product(product: ProductRecord) -> ProductFacts:
    """Reconstruct facts without promoting name evidence to manufacturer data."""
    updated = product.synkka_updated_at
    if isinstance(updated, datetime):
        updated = updated.date()
    structured = {code for code, source in product.claims.items() if source == "STRUCTURED"}
    named = {code for code, source in product.claims.items() if source == "NAME_ONLY"}
    named.update(
        code for code, pattern in NAME_PATTERNS.items()
        if re.search(pattern, product.name_en, re.IGNORECASE)
    )
    return ProductFacts(
        product_id=product.id,
        has_synkka=product.has_synkka,
        synkka_updated_at=updated,
        structured_claims=structured,
        has_claim_data=bool(structured),
        name_claims=named,
        allergens={
            code: status for code, (status, _) in product.allergens.items()
            if status in {"CONTAINS", "MAY_CONTAIN", "FREE_FROM"}
        },
        allergen_list_present=bool(product.allergens) or product.allergen_list_present,
    )


def requirement_check(
    product: ProductRecord, requirement: Requirement, today: date,
) -> RequirementCheck:
    """Evaluate one requirement with current freshness and risk-first conflicts.

    The older trust helper checks positive claims first.  Here an explicit
    manufacturer allergen risk wins over a contradictory positive claim, and
    recorded conflicts cannot become an A-grade safe declaration.
    """
    facts = facts_from_product(product)
    code = requirement.code
    if requirement.kind == "LABEL":
        labels = set(product.labels) - NON_CERTIFICATION_LABELS
        present = code in labels
        fresh = (
            facts.synkka_updated_at is not None
            and 0 <= (today - facts.synkka_updated_at).days <= 730
        )
        if code in NON_CERTIFICATION_LABELS:
            result = RequirementCheck(
                "CLAIM", code, "NOT_CLAIMED", "D", "UNVERIFIED",
                "Storage temperature markers are not certification labels",
            )
        elif present and product.has_synkka:
            grade = "A" if fresh else "B"
            result = RequirementCheck(
                "CLAIM", code, "CLAIMED", grade,
                "SATISFIED" if grade == "A" else "UNVERIFIED",
                "Manufacturer packaging label",
            )
        elif product.has_synkka and labels:
            result = RequirementCheck(
                "CLAIM", code, "NOT_CLAIMED", "A" if fresh else "B",
                "NOT_MATCHED", "Not among the manufacturer's packaging labels",
            )
        else:
            result = RequirementCheck(
                "CLAIM", code, "NOT_CLAIMED", "D", "UNVERIFIED", "No label evidence",
            )
    elif requirement.kind == "ALLERGEN_FREE":
        result = check_allergen_free(facts, code, today)
    else:
        allergen = CLAIM_TO_ALLERGEN.get(code)
        status = facts.allergens.get(allergen)
        if status in {"CONTAINS", "MAY_CONTAIN"}:
            result = check_allergen_free(facts, allergen, today)
            result = replace(
                result, kind="CLAIM", code=code,
                conflict=code in facts.structured_claims or code in facts.name_claims,
            )
        else:
            result = check_claim(facts, code, today)

    conflicts = {"ALLERGEN_CONFLICT", "NAME_CLAIM_CONFLICT"}.intersection(product.flags)
    if result.conflict or (conflicts and result.verdict == "SATISFIED"):
        result = replace(
            result, grade="D", conflict=True,
            verdict="VIOLATED" if result.verdict == "VIOLATED" else "UNVERIFIED",
            source=result.source + "; conflicting catalogue evidence requires review",
        )
    future_date = facts.synkka_updated_at is not None and facts.synkka_updated_at > today
    if not product.has_synkka or future_date:
        result = replace(
            result, grade="D",
            verdict="VIOLATED" if result.verdict == "VIOLATED" else "UNVERIFIED",
        )
    return result


def combined_requirements(*groups: Iterable[Requirement]) -> list[Requirement]:
    """Keep every distinct hard requirement in deterministic order."""
    values: dict[tuple[str, str], Requirement] = {}
    for group in groups:
        for requirement in group:
            values.setdefault((requirement.kind, requirement.code), requirement)
    return [values[key] for key in sorted(values)]


class CatalogRepository:
    def __init__(self, db_path: str | Path = DEFAULT_DATABASE, *, today: date | None = None):
        self.db_path = Path(db_path).resolve()
        self.today = today or datetime.now(HELSINKI).date()

    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.is_file():
            raise FileNotFoundError(
                f"Catalogue not found: {self.db_path}. Run backend/scripts/preprocess.py first."
            )
        connection = sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection

    def _records(self, connection: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[ProductRecord]:
        if not rows:
            return []
        related = {
            "product_claims": {}, "product_allergens": {},
            "product_labels": {}, "product_flags": {},
        }
        ids = [row["id"] for row in rows]
        # SQLite builds differ in the maximum parameter count; small batches
        # work on both older installations and the current development runtime.
        for offset in range(0, len(ids), 500):
            batch = ids[offset:offset + 500]
            placeholders = ",".join("?" for _ in batch)
            for table, index in related.items():
                for row in connection.execute(
                    f"SELECT * FROM {table} WHERE product_id IN ({placeholders})", batch,
                ):
                    index.setdefault(row["product_id"], []).append(row)

        records = []
        for row in rows:
            product_id = row["id"]
            claims = {
                item["claim"]: item["source"]
                for item in related["product_claims"].get(product_id, [])
            }
            allergen_rows = related["product_allergens"].get(product_id, [])
            allergens = {
                item["code"]: (item["status"], item["trust_grade"])
                for item in allergen_rows
            }
            try:
                updated = (
                    datetime.fromisoformat(row["synkka_updated_at"])
                    if row["synkka_updated_at"] else None
                )
            except ValueError:
                updated = None
            record = ProductRecord(
                id=product_id, name_en=row["name_en"], brand=row["brand"],
                vendor=row["vendor"], category=row["category"], sales_unit=row["sales_unit"],
                lot_size=row["lot_size"], qty_per_sales_unit=row["qty_per_sales_unit"],
                qty_unit=row["qty_unit"], qty_source=row["qty_source"] or "UNKNOWN",
                claims=claims, allergens=allergens,
                labels=sorted(
                    item["label"] for item in related["product_labels"].get(product_id, [])
                    if item["label"] not in NON_CERTIFICATION_LABELS
                ),
                flags=sorted(item["flag"] for item in related["product_flags"].get(product_id, [])),
                substitutions=json.loads(row["substitutions_json"]),
                deposits=json.loads(row["deposits_json"]),
                delivery_reliability=row["delivery_reliability"], co2_kg_per_kg=row["co2_kg_per_kg"],
                has_synkka=bool(row["has_synkka"]), base_unit=row["base_unit"], country=row["country"],
                gtin=row["gtin"], synkka_updated_at=updated, case_volume_l=row["case_volume_l"],
                min_temp_c=row["min_temp_c"], max_temp_c=row["max_temp_c"],
                temperature_condition=row["temperature_condition"],
                has_claim_data=any(source == "STRUCTURED" for source in claims.values()),
                allergen_list_present=bool(allergens),
            )
            # Do not freeze freshness at the preprocessing date: old snapshots
            # must age when read later, including public allergen grades.
            record.allergens = {
                code: (
                    status,
                    requirement_check(
                        record, Requirement(kind="ALLERGEN_FREE", code=code), self.today,
                    ).grade,
                )
                for code, (status, _) in allergens.items()
            }
            records.append(record)
        return records

    def get(self, product_id: str) -> ProductRecord:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchall()
            records = self._records(connection, rows)
        if not records:
            raise ProductNotFoundError(product_id)
        return records[0]

    def get_facts(self, product_id: str) -> ProductFacts:
        return facts_from_product(self.get(product_id))

    def _category_products(self, category: str) -> list[ProductRecord]:
        with closing(self._connect()) as connection:
            return self._records(connection, connection.execute(
                "SELECT * FROM products WHERE category = ? ORDER BY id", (category,),
            ).fetchall())

    @staticmethod
    def _exclusions(product: ProductRecord, rules: EffectiveRules) -> list[Violation]:
        violations = []
        checks = (
            ("PROFILE_EXCLUDED_PRODUCT", product.id, rules.excluded_product_ids),
            ("PROFILE_EXCLUDED_BRAND", product.brand, rules.excluded_brands),
            ("PROFILE_EXCLUDED_VENDOR", product.vendor, rules.excluded_vendors),
        )
        for rule, value, excluded in checks:
            if value is not None and value.casefold() in {entry.casefold() for entry in excluded}:
                violations.append(Violation(
                    rule=rule, detail=f"{value} is excluded by the effective profile", product_id=product.id,
                ))
        return violations

    def filter(self, req: StructuredRequest, rules: EffectiveRules) -> FilterResult:
        products = self._category_products(req.category)
        requirements = combined_requirements(
            rules.protocol.hard_requirements, rules.hard_requirements, req.requirements,
        )
        kept: list[ProductRecord] = []
        blocked: list[tuple[ProductRecord, list[Violation]]] = []
        dropped = 0
        for product in products:
            violations = self._exclusions(product, rules)
            if product.category not in rules.protocol.allowed_categories:
                violations.append(Violation(
                    rule="PROTOCOL_CATEGORY_FORBIDDEN", detail="Category is outside the protocol scope",
                    product_id=product.id,
                ))
            checks = [requirement_check(product, requirement, self.today) for requirement in requirements]
            for requirement, check in zip(requirements, checks):
                if check.verdict == "VIOLATED":
                    violations.append(Violation(
                        rule=f"ALLERGEN_{check.status}",
                        detail=f"{requirement.code}: {check.source}", product_id=product.id,
                    ))
            if violations:
                blocked.append((product, violations))
            elif any(check.verdict == "NOT_MATCHED" for check in checks):
                dropped += 1
            else:
                kept.append(product)
        event = ChainEvent(
            at=datetime.combine(self.today, time.min, tzinfo=timezone.utc),
            title="Filtered the static catalogue",
            logic=f"Kept {len(kept)}, blocked {len(blocked)}, and dropped {dropped} products",
            evidence=[EvidenceRef(
                source="CATALOG", detail=f"Category {req.category}; structured requirements and profile exclusions",
            )],
        )
        return FilterResult(kept=kept, blocked=blocked, dropped_count=dropped, events=[event])

    def official_substitutes(self, product_id: str) -> list[ProductRecord]:
        product = self.get(product_id)
        substitutes = []
        for target_id in sorted(set(product.substitutions) - {product_id}):
            try:
                substitutes.append(self.get(target_id))
            except ProductNotFoundError:
                # Missing external references are reported during preprocessing.
                continue
        return substitutes

    def same_category_alternatives(
        self, product: ProductRecord, rules: EffectiveRules, limit: int = 5,
    ) -> list[ProductRecord]:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        if product.category not in rules.protocol.allowed_categories or limit == 0:
            return []
        requirements = combined_requirements(rules.protocol.hard_requirements, rules.hard_requirements)
        alternatives = []
        for alternative in self._category_products(product.category):
            if alternative.id == product.id or self._exclusions(alternative, rules):
                continue
            checks = [requirement_check(alternative, requirement, self.today) for requirement in requirements]
            if any(check.verdict in {"VIOLATED", "NOT_MATCHED"} for check in checks):
                continue
            alternatives.append(alternative)
            if len(alternatives) == limit:
                break
        return alternatives

    def search(self, request: ProductSearchRequest) -> list[ProductRecord]:
        if request.limit < 1 or request.limit > 50:
            raise ValueError("search limit must be between 1 and 50")
        clauses: list[str] = []
        parameters: list[str] = []
        if request.category is not None:
            clauses.append("p.category = ?")
            parameters.append(request.category)
        if request.text:
            tokens = re.findall(r"\w+", request.text, flags=re.UNICODE)
            if not tokens:
                return []
            literal_query = " AND ".join('"' + token + '"' for token in tokens)
            clauses.append("p.id IN (SELECT id FROM products_fts WHERE products_fts MATCH ?)")
            parameters.append(literal_query)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with closing(self._connect()) as connection:
            products = self._records(connection, connection.execute(
                "SELECT p.* FROM products p" + where + " ORDER BY p.id", parameters,
            ).fetchall())
        matches = []
        for product in products:
            checks = [requirement_check(product, requirement, self.today) for requirement in request.requirements]
            if any(check.verdict in {"VIOLATED", "NOT_MATCHED"} for check in checks):
                continue
            matches.append(product)
            if len(matches) == request.limit:
                break
        return matches
