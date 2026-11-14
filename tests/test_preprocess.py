from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from scripts.preprocess import DataQualityError, build_catalog, iter_json_array


AS_OF = date(2026, 11, 14)


def _classification(name: str, values: list[dict]) -> dict:
    return {"type": "ENUM", "name": name, "values": values}


def _synkka(
    *,
    names: list[dict],
    classifications: list[dict] | None = None,
    marketing_text: str = "",
    unit_conversions: list[dict] | None = None,
) -> dict:
    return {
        "gtin": "6400000000001",
        "lastUpdated": "2026-01-15T12:00:00+0200",
        "names": names,
        "classifications": classifications or [],
        "keyIngredients": [],
        "marketingTexts": (
            [{"language": "en", "value": marketing_text}]
            if marketing_text
            else []
        ),
        "materialAdditionalDescriptions": [],
        "medias": [],
        "nutritionalContent": [],
        "unitConversions": unit_conversions or [],
    }


def _product(
    product_id: str,
    *,
    names: list[dict],
    classifications: list[dict] | None = None,
    sales_unit: str = "KI",
    base_unit: str = "ST",
    sales_multiplier: float = 2,
    category: str = "10400",
    marketing_text: str = "",
    unit_conversions: list[dict] | None = None,
) -> dict:
    return {
        "id": product_id,
        "salesUnitGtin": f"6400000{product_id.zfill(6)}",
        "salesUnit": sales_unit,
        "baseUnit": base_unit,
        "category": category,
        "allowedLotSize": 1,
        "deleted": False,
        "temperatureCondition": 4,
        "vendorName": "Test Vendor",
        "countryOfOrigin": "fi",
        "synkkaData": _synkka(
            names=names,
            classifications=classifications,
            marketing_text=marketing_text,
            unit_conversions=unit_conversions,
        ),
        "units": [
            {
                "unitId": sales_unit,
                "gtin": f"6400000{product_id.zfill(6)}",
                "sizeInBaseUnits": sales_multiplier,
            },
            {"unitId": base_unit, "sizeInBaseUnits": 1},
        ],
        "deposits": [],
    }


def _write_inputs(
    root: Path,
    products: list[dict],
    *,
    reliability_rows: str = "p1,0.9\n",
    carbon_rows: str = "p1,1.25\n",
) -> tuple[Path, Path, Path]:
    products_path = root / "products.json"
    reliability_path = root / "reliability.csv"
    carbon_path = root / "carbon.csv"
    products_path.write_text(json.dumps(products), encoding="utf-8")
    reliability_path.write_text(
        "productId,deliveryReliability\n" + reliability_rows,
        encoding="utf-8",
    )
    carbon_path.write_text(
        "productId,AVG kg CO2-eq/kg\n" + carbon_rows,
        encoding="utf-8",
    )
    return products_path, reliability_path, carbon_path


def _build(tmp_path: Path, products: list[dict], **input_overrides):
    products_path, reliability_path, carbon_path = _write_inputs(
        tmp_path, products, **input_overrides
    )
    output_path = tmp_path / "products.sqlite"
    report_path = tmp_path / "products.quality.json"
    report = build_catalog(
        products_path=products_path,
        reliability_path=reliability_path,
        carbon_path=carbon_path,
        output_path=output_path,
        report_path=report_path,
        schema_path=None,
        as_of=AS_OF,
    )
    return output_path, report_path, report


def test_builds_static_catalog_with_conservative_facts(tmp_path: Path):
    classifications = [
        _classification(
            "allergen",
            [
                {"id": "AM", "unit": "CONTAINS"},
                {"id": "AN", "unit": "UNDECLARED"},
            ],
        ),
        _classification(
            "nonAllergen", [{"id": "AM", "unit": "FREE_FROM"}]
        ),
        _classification(
            "nutritionalClaim",
            [{"id": "FREE_FROM_LACTOSE", "unit": "FREE_FROM"}],
        ),
        _classification(
            "packagingMarkedLabel", [{"id": "EU_ORGANIC_FARMING"}]
        ),
    ]
    name_parsed = _product(
        "p1",
        names=[{"language": "fi", "value": "Testimaito 10 l laktoositon"}],
        classifications=classifications,
        sales_multiplier=2,
        # Marketing copy is untrusted and must not become a claim or FTS input.
        marketing_text="secretmarketingtoken vegan gluten-free",
        unit_conversions=[
            {
                "synkkaPackagingType": "CASE",
                "width": {"unit": "MMT", "value": 200},
                "length": {"unit": "MMT", "value": 300},
                "height": {"unit": "MMT", "value": 400},
                "grossWeight": {"unit": "KGM", "value": 22},
            }
        ],
    )
    direct_case = _product(
        "p2",
        names=[{"language": "en", "value": "Direct case 99 l"}],
        sales_multiplier=6,
        unit_conversions=[
            {
                "synkkaPackagingType": "CASE",
                "width": {"unit": "MMT", "value": 100},
                "length": {"unit": "MMT", "value": 200},
                "height": {"unit": "MMT", "value": 300},
                "grossWeight": {"unit": "KGM", "value": 13},
                "volume": {"unit": "LTR", "value": 12},
            }
        ],
    )

    db_path, report_path, report = _build(tmp_path, [name_parsed, direct_case])

    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    assert report["counts"]["products"] == 2
    assert report["counts"]["categories"] == 1
    assert report["unknown"] == {
        "allergen_data_products": 1,
        "claim_data_products": 1,
        "explicit_allergen_rows": 1,
        "quantity": 0,
    }

    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT count(*) FROM products_fts").fetchone()[0] == 2

        first = db.execute(
            """SELECT name_en, qty_per_sales_unit, qty_unit, qty_source,
                      delivery_reliability, co2_kg_per_kg
                 FROM products WHERE id = 'p1'"""
        ).fetchone()
        assert dict(first) == {
            "name_en": "Testimaito 10 l laktoositon",
            "qty_per_sales_unit": 20.0,
            "qty_unit": "L",
            "qty_source": "NAME_PARSE",
            "delivery_reliability": 0.9,
            "co2_kg_per_kg": 1.25,
        }

        second = db.execute(
            """SELECT qty_per_sales_unit, qty_unit, qty_source,
                      delivery_reliability, co2_kg_per_kg
                 FROM products WHERE id = 'p2'"""
        ).fetchone()
        # CASE volume already describes the whole sales case; do not multiply it by 6.
        assert dict(second) == {
            "qty_per_sales_unit": 12.0,
            "qty_unit": "L",
            "qty_source": "UNIT_CONVERSIONS",
            "delivery_reliability": None,
            "co2_kg_per_kg": None,
        }

        claims = db.execute(
            "SELECT claim, source FROM product_claims WHERE product_id = 'p1'"
        ).fetchall()
        assert [tuple(row) for row in claims] == [
            ("FREE_FROM_LACTOSE", "STRUCTURED")
        ]
        labels = db.execute(
            "SELECT label FROM product_labels WHERE product_id = 'p1'"
        ).fetchall()
        assert [tuple(row) for row in labels] == [("EU_ORGANIC_FARMING",)]
        allergens = db.execute(
            """SELECT code, status FROM product_allergens
                 WHERE product_id = 'p1' ORDER BY code"""
        ).fetchall()
        assert [tuple(row) for row in allergens] == [
            ("AM", "CONTAINS"),
            ("AN", "UNDECLARED"),
        ]

        flags = {
            row[0]
            for row in db.execute(
                "SELECT flag FROM product_flags WHERE product_id = 'p1'"
            )
        }
        assert "NAME_EN_FALLBACK" in flags
        assert "ALLERGEN_CONFLICT" in flags

        assert db.execute(
            "SELECT count(*) FROM product_claims WHERE claim = 'VEGAN'"
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT count(*) FROM products_fts WHERE products_fts MATCH ?",
            ("secretmarketingtoken",),
        ).fetchone()[0] == 0


def test_base_conversion_uses_gtin_packaging_level_without_double_counting(
    tmp_path: Path,
):
    product = _product(
        "p1",
        names=[{"language": "en", "value": "Food 2x2kg / 4kg"}],
        sales_multiplier=2,
        unit_conversions=[
            {
                "synkkaPackagingType": "BASE_UNIT_OR_EACH",
                "width": {"unit": "MMT", "value": 100},
                "length": {"unit": "MMT", "value": 200},
                "height": {"unit": "MMT", "value": 300},
                "grossWeight": {"unit": "GRM", "value": 4200},
                "netWeight": {"unit": "GRM", "value": 4000},
            }
        ],
    )
    # The Synkka GTIN is the sales case, so its 4 kg conversion already
    # represents both base bags and must not be multiplied by two again.
    product["synkkaData"]["gtin"] = product["salesUnitGtin"]

    db_path, _, _ = _build(tmp_path, [product])
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT qty_per_sales_unit, qty_unit, qty_source FROM products"
        ).fetchone() == (4.0, "kg", "UNIT_CONVERSIONS")


def test_json_array_reader_crosses_small_chunks(tmp_path: Path):
    source = tmp_path / "array.json"
    expected = [{"name": "maito å", "nested": [1, 2]}, 3, {"ok": True}]
    source.write_text(json.dumps(expected, ensure_ascii=False), encoding="utf-8")

    assert list(iter_json_array(source, chunk_size=3)) == expected


def test_json_array_reader_does_not_commit_a_number_prefix(tmp_path: Path):
    source = tmp_path / "numbers.json"
    source.write_text("[28,[4,4]]", encoding="utf-8")

    assert list(iter_json_array(source, chunk_size=1)) == [28, [4, 4]]


def test_non_food_container_capacity_is_not_treated_as_product_quantity(
    tmp_path: Path,
):
    paper_bags = _product(
        "p1",
        names=[{"language": "en", "value": "Paper bag 26 L"}],
        category="99999",
        sales_multiplier=200,
    )

    db_path, _, _ = _build(tmp_path, [paper_bags])
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT qty_per_sales_unit, qty_unit, qty_source FROM products"
        ).fetchone() == (200.0, "pcs", "UNIT_CONVERSIONS")


def test_repeated_build_is_byte_for_byte_reproducible(tmp_path: Path):
    product = _product(
        "p1", names=[{"language": "en", "value": "Test milk 1 l"}]
    )
    db_path, report_path, first_report = _build(tmp_path, [product])
    first_database = db_path.read_bytes()
    first_report_bytes = report_path.read_bytes()

    _, _, second_report = _build(tmp_path, [product])

    assert second_report == first_report
    assert db_path.read_bytes() == first_database
    assert report_path.read_bytes() == first_report_bytes


def test_product_failure_after_temp_build_preserves_existing_database(
    tmp_path: Path,
):
    product = _product(
        "p1", names=[{"language": "en", "value": "Test milk 1 l"}]
    )
    products_path, reliability_path, carbon_path = _write_inputs(
        tmp_path, [product, product]
    )
    output_path = tmp_path / "products.sqlite"
    report_path = tmp_path / "products.quality.json"
    output_path.write_bytes(b"existing-good-database")

    with pytest.raises(DataQualityError, match="duplicate product id"):
        build_catalog(
            products_path=products_path,
            reliability_path=reliability_path,
            carbon_path=carbon_path,
            output_path=output_path,
            report_path=report_path,
            schema_path=None,
            as_of=AS_OF,
        )

    assert output_path.read_bytes() == b"existing-good-database"
    assert not report_path.exists()


@pytest.mark.parametrize(
    ("reliability_rows", "carbon_rows"),
    [
        ("p1,0.9\np1,0.8\n", "p1,1.25\n"),
        ("p1,0.9\n", "p1,NaN\n"),
        ("p1,1.1\n", "p1,1.25\n"),
        ("p1,0.9\n", "p1,-0.1\n"),
    ],
)
def test_invalid_enrichment_is_rejected_without_replacing_output(
    tmp_path: Path, reliability_rows: str, carbon_rows: str
):
    product = _product(
        "p1", names=[{"language": "en", "value": "Test product 1 l"}]
    )
    products_path, reliability_path, carbon_path = _write_inputs(
        tmp_path,
        [product],
        reliability_rows=reliability_rows,
        carbon_rows=carbon_rows,
    )
    output_path = tmp_path / "products.sqlite"
    report_path = tmp_path / "products.quality.json"
    output_path.write_bytes(b"existing-good-database")

    with pytest.raises(DataQualityError):
        build_catalog(
            products_path=products_path,
            reliability_path=reliability_path,
            carbon_path=carbon_path,
            output_path=output_path,
            report_path=report_path,
            schema_path=None,
            as_of=AS_OF,
        )

    assert output_path.read_bytes() == b"existing-good-database"
    assert not report_path.exists()
