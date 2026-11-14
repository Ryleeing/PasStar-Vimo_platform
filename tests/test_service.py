from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
import sqlite3

import pytest

from app.catalog.cards import CardsBuilder
from app.catalog.repository import CatalogRepository
from app.catalog.service import CatalogService, RankingPolicy, effective_rules
from app.internal import AvailabilityInfo, FilterResult, PriceInfo, ProductRecord
from app.schemas import (
    GateDecision, HardExclusions, ProfileBody, ProtocolBody, Quantity,
    Requirement, StructuredRequest, Violation,
)


TODAY = date(2026, 11, 14)
LACTOSE = Requirement(kind="CLAIM", code="FREE_FROM_LACTOSE")


def product(product_id="milk", **changes):
    record = ProductRecord(
        id=product_id, name_en="Milk 20 L", brand="Test", vendor="Supplier",
        category="10400", sales_unit="KI", lot_size=1, qty_per_sales_unit=20,
        qty_unit="L", qty_source="UNIT_CONVERSIONS",
        claims={"FREE_FROM_LACTOSE": "STRUCTURED"}, allergens={"AM": ("CONTAINS", "A")},
        labels=[], flags=[], substitutions=[], deposits=[],
        delivery_reliability=0.975, co2_kg_per_kg=1.5, has_synkka=True,
        country="fi", synkka_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    return replace(record, **changes)


def protocol(**changes):
    values = dict(
        buyer_id="buyer", agent_id="agent", auto_approve_limit_cents=100_000,
        max_order_cents=300_000, allowed_categories=["10400"],
        hard_requirements=[LACTOSE], approver="manager", valid_until=date(2027, 1, 1),
    )
    values.update(changes)
    return ProtocolBody(**values)


def request(**changes):
    values = dict(
        category="10400", quantity=Quantity(value=1000, unit="L"),
        requirements=[LACTOSE], delivery_date=date(2026, 11, 16),
    )
    values.update(changes)
    return StructuredRequest(**values)


class Catalog:
    def __init__(self, products, blocked=None):
        self.products = {item.id: item for item in products}
        self.blocked = blocked or []
        self.last_rules = None

    def get(self, product_id):
        return self.products[product_id]

    def filter(self, req, rules):
        self.last_rules = rules
        blocked_ids = {item.id for item, _ in self.blocked}
        return FilterResult(
            [item for item in self.products.values() if item.id not in blocked_ids],
            self.blocked, 0, [],
        )

    def search(self, req):
        return list(self.products.values())[:req.limit]


class Dynamic:
    def __init__(self, *, price=None, availability=None):
        self.price = price or PriceInfo(1760, 0.135, 0)
        self.stock = availability or AvailabilityInfo("YES", None)
        self.calls = []

    def prices(self, ids, buyer):
        self.calls.append(("prices", list(ids), buyer))
        return {pid: self.price for pid in ids}

    def availability(self, ids, day, amounts, agent):
        self.calls.append(("stock", list(ids), day, dict(amounts), agent))
        return {pid: self.stock for pid in ids}


class AutoGate:
    def preview(self, lines, gross, rules, unverified):
        return GateDecision(outcome="AUTO", rules_hit=[], summary="External preview")


def service(products=None, **kwargs):
    catalog = Catalog(products or [product()])
    return CatalogService(catalog, today=TODAY, **kwargs), catalog


def test_profile_merge_unions_constraints_and_does_not_mutate_inputs():
    body = protocol()
    profile = ProfileBody(
        name="Kitchen", hard_exclusions=HardExclusions(
            brands=["Arla"], requirements=[Requirement(kind="ALLERGEN_FREE", code="AN")]
        ),
    )
    merged = effective_rules(body, profile)
    assert {(item.kind, item.code) for item in merged.hard_requirements} == {
        ("CLAIM", "FREE_FROM_LACTOSE"), ("ALLERGEN_FREE", "AN")
    }
    assert merged.excluded_brands == ["Arla"]
    merged.protocol.allowed_categories.clear()
    merged.excluded_brands.append("Other")
    assert body.allowed_categories == ["10400"]
    assert profile.hard_exclusions.brands == ["Arla"]


def test_static_pipeline_returns_cards_without_fabricated_prices_or_stock():
    app, _ = service()
    result = app.generate(request(), effective_rules(protocol()), request_id="req")
    candidate = result.candidates[0]
    assert candidate.lines[0].sales_units == 50
    assert candidate.lines[0].qty_covered.value == 1000
    assert candidate.lines[0].available_on_date == "UNKNOWN"
    assert candidate.lines[0].unit_price_cents is None
    assert candidate.totals is None
    assert candidate.outcome_preview == "VALIO_REVIEW"
    assert candidate.cards.trust.overall == "LOW"
    assert "0.975" not in candidate.model_dump_json()
    assert candidate.id == app.generate(
        request(), effective_rules(protocol()), request_id="req"
    ).candidates[0].id


def test_injected_dynamic_prices_totals_and_gate_are_combined():
    dynamic = Dynamic()
    app, _ = service(valio=dynamic, gate=AutoGate())
    result = app.generate(request(), effective_rules(protocol()))
    candidate = result.candidates[0]
    assert candidate.totals.model_dump() == dict(
        net_cents=88_000, vat_cents=11_880, deposit_cents=0, gross_cents=99_880
    )
    assert candidate.outcome_preview == "AUTO"
    assert candidate.reliability_level == "UNKNOWN"
    assert dynamic.calls[1][3] == {"milk": 50.0}


def test_only_eight_safe_shortlisted_products_are_queried():
    products = [product(f"p{index:02d}") for index in range(15)]
    bad = product("blocked")
    catalog = Catalog([*products, bad], [(
        bad, [Violation(rule="PROFILE_EXCLUDED_BRAND", detail="Excluded", product_id=bad.id)]
    )])
    dynamic = Dynamic()
    app = CatalogService(catalog, valio=dynamic, today=TODAY)
    result = app.generate(request(), effective_rules(protocol()))
    assert len(result.candidates) == 8
    assert [item.rank for item in result.candidates] == list(range(1, 9))
    assert all(len(call[1]) == 8 and "blocked" not in call[1] for call in dynamic.calls)
    assert len(result.blocked) == 1
    assert result.blocked[0].selectable is False
    assert result.blocked[0].rank is None
    assert result.blocked[0].violations[0].rule == "PROFILE_EXCLUDED_BRAND"


def test_unconvertible_product_is_reported_and_never_queried():
    dynamic = Dynamic()
    app, _ = service([product(qty_per_sales_unit=None, qty_source="UNKNOWN")], valio=dynamic)
    result = app.generate(request(), effective_rules(protocol()))
    assert result.candidates == []
    assert result.unconvertible_product_ids == ["milk"]
    assert result.no_candidate_reason
    assert dynamic.calls == []


def test_unknown_quantity_does_not_lose_blocked_violations():
    bad = product(qty_per_sales_unit=None, qty_source="UNKNOWN")
    violations = [Violation(rule="PROFILE_EXCLUDED_BRAND", detail="Excluded")]
    app = CatalogService(Catalog([bad], [(bad, violations)]), today=TODAY)
    result = app.generate(request(), effective_rules(protocol()))
    assert result.unconverted_blocked == {"milk": violations}
    assert result.unconvertible_product_ids == ["milk"]
    assert result.blocked[0].lines == []
    assert result.blocked[0].violations == violations
    assert not result.blocked[0].selectable


@pytest.mark.parametrize(
    ("record", "stock", "body_changes", "expected_rule", "blocked"),
    [
        (product(qty_source="NAME_PARSE"), AvailabilityInfo("YES", None), {}, "R-QTY-INEXACT", False),
        (product(), AvailabilityInfo("UNKNOWN", None), {}, "R-DELIVERY-UNKNOWN", False),
        (product(), AvailabilityInfo("NO", None), {}, "R-DELIVERY-NONE", True),
        (product(claims={}), AvailabilityInfo("YES", None), {"unknown_data_policy": "REJECT"}, "R-DATA-UNVERIFIED", True),
        (product(), AvailabilityInfo("YES", None), {"valid_until": date(2025, 1, 1)}, "R-PROTOCOL-EXPIRED", True),
    ],
)
def test_even_an_auto_gate_cannot_weaken_required_safety_preview(
    record, stock, body_changes, expected_rule, blocked,
):
    app, _ = service([record], valio=Dynamic(availability=stock), gate=AutoGate())
    result = app.generate(request(), effective_rules(protocol(**body_changes)))
    candidate = (result.blocked if blocked else result.candidates)[0]
    assert candidate.outcome_preview != "AUTO"
    assert expected_rule in candidate.outcome_reasons


def test_partial_deliverable_is_capped_and_only_disclosed_for_partial():
    app, _ = service(valio=Dynamic(availability=AvailabilityInfo("PARTIAL", 999999)), gate=AutoGate())
    candidate = app.generate(request(), effective_rules(protocol())).candidates[0]
    assert candidate.lines[0].deliverable_sales_units == 50
    assert candidate.outcome_preview == "BUYER_APPROVAL"


def test_request_budget_tightens_protocol_and_unpriced_deposit_cannot_understate_total():
    app, _ = service(valio=Dynamic(), gate=AutoGate())
    result = app.generate(request(budget_cents=99_000), effective_rules(protocol()))
    assert result.candidates == []
    assert result.blocked[0].outcome_reasons[-1] == "R-SPEND-MAX"

    app, _ = service([product(deposits=[{"material": "1", "quantity": 1, "unit": "ST"}])],
                     valio=Dynamic(), gate=AutoGate())
    candidate = app.generate(request(), effective_rules(protocol())).candidates[0]
    assert candidate.totals is None
    assert candidate.outcome_preview == "VALIO_REVIEW"


def test_public_product_projection_never_includes_raw_reliability():
    app, _ = service()
    payload = app.get_product("milk").model_dump()
    assert payload["id"] == "milk"
    assert "delivery_reliability" not in payload
    assert "co2_kg_per_kg" not in payload
    assert "vendor" not in payload


def test_public_product_does_not_promote_name_claims_or_storage_codes():
    record = product(
        claims={"FREE_FROM_LACTOSE": "NAME_ONLY", "VEGETARIAN": "STRUCTURED"},
        labels=["coolTemperature", "EU_ORGANIC_FARMING"],
    )
    app, _ = service([record])
    payload = app.get_product(record.id)
    assert payload.claims == ["VEGETARIAN"]
    assert payload.labels == ["EU_ORGANIC_FARMING"]


def test_name_derived_public_quantity_keeps_an_inference_flag():
    app, _ = service([product(qty_source="NAME_PARSE")])
    payload = app.get_product("milk")
    assert payload.qty_per_sales_unit == 20
    assert "QUANTITY_NAME_INFERRED" in payload.flags


def test_sqlite_to_candidates_and_cards_integration(tmp_path):
    path = tmp_path / "catalog.sqlite"
    with sqlite3.connect(path) as db:
        schema = Path(__file__).resolve().parents[1] / "backend/app/schema.sql"
        db.executescript(schema.read_text())
        for pid, category, brand in [
            ("463038", "10400", "Valio"), ("excluded", "10400", "Arla"),
            ("powder", "10499", "Valio"),
        ]:
            db.execute(
                """INSERT INTO products
                (id,name_en,brand,vendor,category,sales_unit,base_unit,lot_size,
                 units_json,qty_per_sales_unit,qty_unit,qty_source,has_synkka,synkka_updated_at)
                VALUES (?, 'Milk 10 L lactose free', ?, 'Supplier', ?, 'KI', 'ST', .01,
                        '[]', 20, 'L', 'NAME_PARSE', 1, '2025-09-29T20:01:27+0300')""",
                (pid, brand, category),
            )
            db.execute("INSERT INTO product_claims VALUES (?, 'FREE_FROM_LACTOSE', 'STRUCTURED')", (pid,))
            db.execute("INSERT INTO product_allergens VALUES (?, 'AM', 'CONTAINS', 'A')", (pid,))

    catalog = CatalogRepository(path, today=TODAY)
    app = CatalogService(catalog, today=TODAY)
    rules = effective_rules(protocol(), ProfileBody(
        name="Kitchen", hard_exclusions=HardExclusions(brands=["Arla"])
    ))
    result = app.generate(request(), rules)
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.lines[0].product_id == "463038"
    assert candidate.lines[0].sales_units == 50
    assert candidate.lines[0].qty_covered.value == 1000
    assert candidate.lines[0].conversion_source == "NAME_PARSE"
    assert candidate.cards.allergen.rows[0].trust_grade == "A"
    assert candidate.cards.trust.evidence == "LOW"
    assert result.blocked[0].lines[0].product_id == "excluded"
    assert result.blocked[0].violations[0].rule == "PROFILE_EXCLUDED_BRAND"


def test_weighted_ranking_is_explicit_opt_in_and_rejects_bad_weights():
    body = protocol()
    rules = effective_rules(body)
    rules.weights["price"] = -1
    app, _ = service(ranking=RankingPolicy(mode="weighted"))
    with pytest.raises(ValueError, match="non-negative"):
        app.generate(request(), rules)


def test_rejected_options_do_not_change_selectable_weighted_ranking():
    class ProductPrices(Dynamic):
        def prices(self, ids, buyer):
            return {pid: PriceInfo({"a": 100, "b": 200, "c": 100_000}[pid], .135, 0)
                    for pid in ids}

    products = [product("a", co2_kg_per_kg=2), product("b", co2_kg_per_kg=1)]
    kwargs = dict(valio=ProductPrices(), gate=AutoGate(), ranking=RankingPolicy(mode="weighted"))
    before, _ = service(products, **kwargs)
    after, _ = service([*products, product("c", co2_kg_per_kg=1.5)], **kwargs)
    first = before.generate(request(), effective_rules(protocol()))
    second = after.generate(request(), effective_rules(protocol()))
    assert [item.lines[0].product_id for item in first.candidates] == ["a", "b"]
    assert [item.lines[0].product_id for item in second.candidates] == ["a", "b"]
    assert second.blocked[0].lines[0].product_id == "c"
