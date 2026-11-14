from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest

from app.catalog.repository import CatalogRepository, ProductNotFoundError, requirement_check
from app.catalog import repository as repository_module
from app.internal import EffectiveRules
from app.schemas import ProductSearchRequest, ProtocolBody, Quantity, Requirement, StructuredRequest


TODAY = date(2026, 11, 14)
LACTOSE = Requirement(kind="CLAIM", code="FREE_FROM_LACTOSE")
NUTS = Requirement(kind="ALLERGEN_FREE", code="AN")
MILK = Requirement(kind="ALLERGEN_FREE", code="AM")


def rules(*requirements, **updates):
    protocol = ProtocolBody(
        buyer_id="buyer", agent_id="agent", auto_approve_limit_cents=100_000,
        max_order_cents=200_000, allowed_categories=["10400"],
        approver="manager", valid_until=date(2027, 1, 1),
    )
    result = EffectiveRules(
        protocol=protocol, hard_requirements=list(requirements),
        excluded_brands=[], excluded_vendors=[], excluded_product_ids=[],
        auto_limit_cents=100_000, max_cents=200_000,
        weights={"price": .4, "fulfilment": .4, "carbon": .1, "local": .1}, soft={},
    )
    return replace(result, **updates)


def request(*requirements, category="10400"):
    return StructuredRequest(
        category=category, requirements=list(requirements),
        quantity=Quantity(value=1000, unit="L"), delivery_date=date(2026, 11, 16),
    )


@pytest.fixture
def repository(tmp_path):
    db_path = tmp_path / "catalog.sqlite"
    with sqlite3.connect(db_path) as db:
        schema = Path(__file__).resolve().parents[1] / "backend/app/schema.sql"
        db.executescript(schema.read_text())
        rows = [
            ("a", "Valio milk 10 L lactose free", "Valio", "10400"),
            ("b", "Unverified milk", None, "10400"),
            ("c", "Ordinary milk", "Arla", "10400"),
            ("d", "Milk powder", "Valio", "10499"),
            ("e", "Name milk lactose free", "Test", "10400"),
            ("f", "Different claim milk", "Test", "10400"),
        ]
        for product_id, name, brand, category in rows:
            db.execute(
                """INSERT INTO products
                   (id,name_en,brand,vendor,category,sales_unit,base_unit,lot_size,
                    units_json,qty_per_sales_unit,qty_unit,qty_source,has_synkka,
                    synkka_updated_at,country,min_temp_c,max_temp_c,case_volume_l,
                    substitutions_json,deposits_json,delivery_reliability)
                   VALUES (?,?,?,?,?,'KI','ST',1,'[]',20,'L','NAME_PARSE',1,
                           '2026-01-01T12:00:00+0200','fi',0,6,24,?,'[]',0.9)""",
                (product_id, name, brand, "Vendor", category, json.dumps(["b", "c", "missing"])),
            )
            db.execute(
                "INSERT INTO products_fts (id,name_en,brand) VALUES (?,?,?)",
                (product_id, name, brand),
            )
        db.executemany(
            "INSERT INTO product_claims VALUES (?,?,?)",
            [("a", "FREE_FROM_LACTOSE", "STRUCTURED"),
             ("e", "FREE_FROM_LACTOSE", "NAME_ONLY"),
             ("f", "VEGETARIAN", "STRUCTURED")],
        )
        db.executemany(
            "INSERT INTO product_allergens VALUES (?,?,?,?)",
            [("a", "AM", "CONTAINS", "A"), ("a", "AN", "FREE_FROM", "A"),
             ("c", "ML", "CONTAINS", "A"), ("b", "AN", "UNDECLARED", "B")],
        )
        db.execute("INSERT INTO product_labels VALUES ('a','EU_ORGANIC_FARMING')")
    return CatalogRepository(db_path, today=TODAY)


def test_get_hydrates_metadata_and_keeps_missing_facts(repository):
    product = repository.get("a")
    assert product.country == "fi"
    assert product.min_temp_c == 0
    assert product.max_temp_c == 6
    assert product.case_volume_l == 24
    assert product.synkka_updated_at.year == 2026
    assert product.claims == {"FREE_FROM_LACTOSE": "STRUCTURED"}
    assert product.labels == ["EU_ORGANIC_FARMING"]
    assert product.gtin is None
    assert repository.get("b").brand is None
    assert not repository.get_facts("b").has_claim_data
    with pytest.raises(ProductNotFoundError):
        repository.get("not-found")


def test_connections_are_read_only_and_missing_database_not_created(repository, tmp_path):
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        with repository._connect() as db:
            db.execute("DELETE FROM products")
    absent = tmp_path / "absent.sqlite"
    with pytest.raises(FileNotFoundError):
        CatalogRepository(absent).get("a")
    assert not absent.exists()


def test_structured_filter_keeps_unknown_blocks_risk_and_drops_nonmatching(repository):
    result = repository.filter(request(LACTOSE), rules())
    assert [product.id for product in result.kept] == ["a", "b", "e"]
    assert [product.id for product, _ in result.blocked] == ["c"]
    assert result.dropped_count == 1
    assert all(product.category == "10400" for product in result.kept)
    assert result.events[0].evidence[0].source == "CATALOG"
    assert requirement_check(repository.get("e"), LACTOSE, TODAY).grade == "C"


def test_request_protocol_and_profile_requirements_all_apply(repository):
    effective = rules(NUTS)
    effective.protocol.hard_requirements = [MILK]
    result = repository.filter(request(LACTOSE), effective)
    assert "a" not in [product.id for product in result.kept]
    assert "a" in [product.id for product, _ in result.blocked]
    assert any(
        violation.rule == "ALLERGEN_CONTAINS"
        for product, violations in result.blocked if product.id == "a" for violation in violations
    )


def test_profile_exclusions_win_over_valid_claim(repository):
    result = repository.filter(
        request(LACTOSE), rules(excluded_brands=["vALIO"], excluded_product_ids=["b"]),
    )
    violations = {product.id: items for product, items in result.blocked}
    assert violations["a"][0].rule == "PROFILE_EXCLUDED_BRAND"
    assert violations["b"][0].rule == "PROFILE_EXCLUDED_PRODUCT"


def test_outside_protocol_category_never_kept(repository):
    result = repository.filter(request(category="10499"), rules())
    assert result.kept == []
    assert result.blocked[0][0].id == "d"
    assert result.blocked[0][1][0].rule == "PROTOCOL_CATEGORY_FORBIDDEN"


def test_allergen_risk_wins_over_contradictory_structured_claim(repository):
    product = repository.get("a")
    product.claims["FREE_FROM_MILK"] = "STRUCTURED"
    check = requirement_check(product, Requirement(kind="CLAIM", code="FREE_FROM_MILK"), TODAY)
    assert (check.status, check.grade, check.verdict) == ("CONTAINS", "D", "VIOLATED")
    product.flags.append("ALLERGEN_CONFLICT")
    assert requirement_check(product, NUTS, TODAY).verdict == "UNVERIFIED"


def test_missing_and_stale_allergen_evidence_never_becomes_safe(repository):
    unknown = requirement_check(repository.get("b"), NUTS, TODAY)
    assert (unknown.status, unknown.verdict) == ("UNDECLARED", "UNVERIFIED")
    stale_repository = CatalogRepository(repository.db_path, today=date(2029, 1, 1))
    assert stale_repository.get("a").allergens["AN"] == ("FREE_FROM", "B")
    assert requirement_check(stale_repository.get("a"), NUTS, date(2029, 1, 1)).verdict == "UNVERIFIED"
    no_manufacturer = replace(repository.get("a"), has_synkka=False)
    assert requirement_check(no_manufacturer, MILK, TODAY).grade == "D"
    assert requirement_check(no_manufacturer, MILK, TODAY).verdict == "VIOLATED"


def test_substitutes_and_alternatives_are_stable_and_filter_exclusions(repository):
    assert [product.id for product in repository.official_substitutes("a")] == ["b", "c"]
    assert [product.id for product in repository.same_category_alternatives(
        repository.get("a"), rules(LACTOSE, excluded_product_ids=["b"]), limit=2,
    )] == ["e"]
    assert repository.same_category_alternatives(repository.get("a"), rules(), limit=0) == []


def test_search_fts_is_literal_and_cannot_escape_category(repository):
    matches = repository.search(ProductSearchRequest(category="10400", text="milk", limit=50))
    assert [product.id for product in matches] == ["a", "b", "c", "e", "f"]
    # Operators and punctuation are treated as literal tokens, not FTS syntax.
    assert repository.search(ProductSearchRequest(text='" OR milk -*')) == []
    assert repository.search(ProductSearchRequest(text="***")) == []
    matches = repository.search(ProductSearchRequest(category="10400", requirements=[LACTOSE]))
    assert [product.id for product in matches] == ["a", "b", "e"]


def test_label_evidence_uses_manufacturer_freshness(repository):
    label = Requirement(kind="LABEL", code="EU_ORGANIC_FARMING")
    assert requirement_check(repository.get("a"), label, TODAY).verdict == "SATISFIED"
    assert requirement_check(repository.get("b"), label, TODAY).verdict == "UNVERIFIED"


def test_temperature_markers_are_not_certifications(repository):
    with sqlite3.connect(repository.db_path) as db:
        db.executemany(
            "INSERT INTO product_labels VALUES (?,?)",
            [("a", "coolTemperature"), ("b", "warmTemperature"), ("b", "freezeTemperature")],
        )
    assert repository.get("a").labels == ["EU_ORGANIC_FARMING"]
    assert repository.get("b").labels == []
    product = replace(repository.get("b"), labels=["coolTemperature"])
    certification = Requirement(kind="LABEL", code="EU_ORGANIC_FARMING")
    check = requirement_check(product, certification, TODAY)
    assert (check.grade, check.verdict) == ("D", "UNVERIFIED")
    for code in repository_module.NON_CERTIFICATION_LABELS:
        check = requirement_check(
            replace(product, labels=[code, "EU_ORGANIC_FARMING"]),
            Requirement(kind="LABEL", code=code), TODAY,
        )
        assert (check.grade, check.verdict) == ("D", "UNVERIFIED")


def test_future_date_risk_is_grade_d_and_still_violated(repository):
    product = replace(repository.get("a"), synkka_updated_at=datetime(2027, 1, 1))
    check = requirement_check(product, MILK, TODAY)
    assert (check.status, check.grade, check.verdict) == ("CONTAINS", "D", "VIOLATED")
    assert requirement_check(product, NUTS, TODAY).verdict == "UNVERIFIED"


def test_default_today_uses_helsinki_timezone(repository, monkeypatch):
    seen = []

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            seen.append(tz)
            return cls(2026, 11, 15, 0, 15, tzinfo=tz)

    monkeypatch.setattr(repository_module, "datetime", Clock)
    default = CatalogRepository(repository.db_path)
    assert default.today == date(2026, 11, 15)
    assert str(seen[0]) == "Europe/Helsinki"
