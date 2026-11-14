from dataclasses import replace

import pytest

from app.catalog.units import UnitsConverter, convert
from app.internal import ProductRecord
from app.schemas import Quantity


def product(**updates):
    base = ProductRecord(
        id="463038",
        name_en="Milk 10 l",
        brand="Valio",
        vendor="Valio",
        category="10400",
        sales_unit="KI",
        lot_size=0.01,
        qty_per_sales_unit=20.0,
        qty_unit="L",
        qty_source="UNIT_CONVERSIONS",
        claims={},
        allergens={},
        labels=[],
        flags=[],
        substitutions=[],
        deposits=[],
        delivery_reliability=None,
        co2_kg_per_kg=None,
        has_synkka=True,
    )
    return replace(base, **updates)


def test_golden_milk_conversion_covers_one_thousand_litres():
    result = UnitsConverter().convert(product(), Quantity(value=1000, unit="L"))

    assert result.sales_units == 50
    assert result.qty_covered == Quantity(value=1000, unit="L")
    assert result.overbuy is None
    assert result.source == "UNIT_CONVERSIONS"
    assert result.exact


def test_decimal_lot_ceiling_does_not_add_an_unnecessary_lot():
    result = convert(
        product(qty_per_sales_unit=0.1, lot_size=0.01),
        Quantity(value=0.003, unit="L"),
    )

    assert result.sales_units == 0.03
    assert result.qty_covered.value == 0.003
    assert result.overbuy is None


def test_rounding_surplus_is_explicit_and_structured_conversion_is_exact():
    result = convert(
        product(qty_per_sales_unit=12, lot_size=1), Quantity(value=25, unit="L")
    )

    assert result.sales_units == 3
    assert result.qty_covered.value == 36
    assert result.overbuy == Quantity(value=11, unit="L")
    assert result.exact


def test_name_parsed_quantity_retains_unverified_provenance():
    result = convert(
        product(qty_source="NAME_PARSE"), Quantity(value=1000, unit="L")
    )

    assert result.sales_units == 50
    assert result.source == "NAME_PARSE"
    assert not result.exact


@pytest.mark.parametrize(
    "updates",
    [
        {"qty_per_sales_unit": None},
        {"qty_source": "UNKNOWN"},
        {"qty_unit": "kg"},
        {"qty_unit": "pcs"},
    ],
)
def test_unknown_or_incompatible_quantity_never_uses_a_density_guess(updates):
    requested = Quantity(value=1000, unit="L")
    result = convert(product(**updates), requested)

    assert result.sales_units == 0
    assert result.source == "UNKNOWN"
    assert not result.exact
    assert result.overbuy is None
    assert result.qty_covered is None


def test_piece_count_rounds_to_the_supplied_lot_size():
    result = convert(
        product(qty_per_sales_unit=6, qty_unit="pcs", lot_size=1),
        Quantity(value=7, unit="pcs"),
    )
    assert result.sales_units == 2
    assert result.qty_covered.value == 12
    assert result.overbuy.value == 5


@pytest.mark.parametrize("field", ["lot_size", "qty_per_sales_unit"])
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True])
def test_invalid_catalog_numbers_raise_value_error(field, value):
    with pytest.raises(ValueError, match=field):
        convert(product(**{field: value}), Quantity(value=1, unit="L"))


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_non_finite_or_non_positive_request_is_rejected(value):
    requested = Quantity.model_construct(value=value, unit="L")
    with pytest.raises(ValueError, match="requested quantity"):
        convert(product(), requested)
