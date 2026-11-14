"""Convert requested quantities into orderable sales-unit lots.

Preprocessing supplies the quantity represented by one sales unit.  Runtime
conversion only applies that explicit quantity and the ordering lot size; it
does not infer packaging codes or a liquid's density.
"""
from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction

from app.internal import Conversion, ProductRecord
from app.schemas import Quantity


def _positive_decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite and positive")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite and positive") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return number


def _finite_float(value: Decimal, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} cannot be represented as a finite positive quantity")
    return number


class UnitsConverter:
    """Implementation of ``UnitsApi`` using exact lot-count arithmetic.

    ``exact`` describes the conversion evidence, not the presence of rounding:
    a structured conversion remains exact when it buys a documented surplus;
    a name-derived amount always remains unverified (``exact=False``).

    An UNKNOWN conversion has ``qty_covered=None`` and ``sales_units=0``:
    coverage has not been established, so it cannot form an orderable line.
    """

    def convert(self, product: ProductRecord, qty: Quantity) -> Conversion:
        requested = _positive_decimal(qty.value, "requested quantity")
        if qty.unit not in {"L", "kg", "pcs"}:
            raise ValueError("requested quantity unit must be L, kg or pcs")
        lot_size = _positive_decimal(product.lot_size, "lot_size")

        capacity = None
        if product.qty_per_sales_unit is not None:
            capacity = _positive_decimal(
                product.qty_per_sales_unit, "qty_per_sales_unit"
            )

        if (
            capacity is None
            or product.qty_source not in {"UNIT_CONVERSIONS", "NAME_PARSE"}
            or product.qty_unit != qty.unit
        ):
            return Conversion(
                sales_units=0.0,
                qty_covered=None,
                overbuy=None,
                source="UNKNOWN",
                exact=False,
            )

        # Decimal(str(...)) preserves supplied decimal lot sizes such as 0.01.
        # Rational ceiling avoids precision-dependent division just below an
        # integer boundary (including non-terminating divisions such as 1/3).
        required_lots = Fraction(requested) / (Fraction(capacity) * Fraction(lot_size))
        lot_count = -(-required_lots.numerator // required_lots.denominator)

        # Multiplication/subtraction are exact at this precision even for very
        # different decimal exponents.  No intermediate binary floats are used.
        values = (requested, capacity, lot_size)
        precision = (
            sum(len(value.as_tuple().digits) for value in values)
            + sum(abs(value.as_tuple().exponent) for value in values)
            + len(str(lot_count))
            + 10
        )
        with localcontext() as context:
            context.prec = max(28, precision)
            sales_units = lot_size * lot_count
            covered = sales_units * capacity
            surplus = covered - requested

        return Conversion(
            sales_units=_finite_float(sales_units, "sales_units"),
            qty_covered=Quantity(
                value=_finite_float(covered, "qty_covered"), unit=qty.unit
            ),
            overbuy=(
                Quantity(value=_finite_float(surplus, "overbuy"), unit=qty.unit)
                if surplus > 0
                else None
            ),
            source=product.qty_source,
            exact=product.qty_source == "UNIT_CONVERSIONS",
        )


def convert(product: ProductRecord, qty: Quantity) -> Conversion:
    """Convenience wrapper for the stateless converter."""
    return UnitsConverter().convert(product, qty)
