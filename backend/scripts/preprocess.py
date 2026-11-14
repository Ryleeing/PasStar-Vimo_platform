"""Build the read-only BE2 product catalogue from the Valio challenge files.

The product export is one large, single-line JSON array.  This module therefore
does not use ``json.load`` for it: :func:`iter_json_array` decodes one array
element at a time from bounded chunks.  The two enrichment CSVs are small and
are loaded into dictionaries before the streaming product pass.

The build is deliberately conservative.  Missing facts stay ``NULL``/UNKNOWN,
net weight is never treated as liquid volume, and name-derived quantities are
labelled ``NAME_PARSE``.  A database is assembled next to the destination and
atomically replaces the previous catalogue only after validation succeeds.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
SCHEMA_SQL = BACKEND_ROOT / "app" / "schema.sql"

# Make ``python backend/scripts/preprocess.py`` work without requiring callers
# to set PYTHONPATH.  Importing as ``scripts.preprocess`` still uses the same
# module below.
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.catalog.trust import (  # noqa: E402  (path is prepared above)
    check_allergen_free,
    check_claim,
    facts_from_raw,
)


PRODUCTS_FILENAME = "valio_aimo_junction26_product_data.json"
PRODUCT_SCHEMA_FILENAME = "valio_aimo_junction26_product_data_schema.json"
RELIABILITY_FILENAME = "valio_aimo_junction26_delivery_reliability.csv"
CARBON_FILENAME = "valio_aimo_junction26_carbon_footprint.csv"

STATIC_TABLES = (
    "products",
    "categories",
    "product_allergens",
    "product_claims",
    "product_labels",
    "product_flags",
    "products_fts",
)

# Name parsing is weaker than manufacturer-provided measures and is unsafe for
# non-food goods (for example a "26 L paper bag" describes container capacity,
# not an orderable food quantity).  Keep it limited to the current demo's milk
# category until reviewed category semantics are available.
NAME_QUANTITY_CATEGORY_ALLOWLIST = {"10400"}


class DataQualityError(ValueError):
    """The source files are readable but violate the catalogue contract."""


def iter_json_array(path: Path, *, chunk_size: int = 1024 * 1024) -> Iterator[Any]:
    """Yield values from a top-level JSON array without loading the whole file.

    ``JSONDecoder.raw_decode`` needs a complete value.  When a value crosses a
    chunk boundary we retain only that unfinished suffix and append another
    chunk; already-yielded values are discarded from memory.
    """

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    decoder = json.JSONDecoder()
    with path.open("r", encoding="utf-8-sig") as stream:
        buffer = ""
        position = 0
        eof = False

        def refill() -> None:
            nonlocal buffer, position, eof
            buffer = buffer[position:]
            position = 0
            chunk = stream.read(chunk_size)
            if chunk:
                buffer += chunk
            else:
                eof = True

        def skip_whitespace() -> None:
            nonlocal position
            while True:
                while position < len(buffer) and buffer[position].isspace():
                    position += 1
                if position < len(buffer) or eof:
                    return
                refill()

        refill()
        skip_whitespace()
        if position >= len(buffer) or buffer[position] != "[":
            raise DataQualityError(f"{path}: expected a top-level JSON array")
        position += 1

        item_index = 0
        first = True
        while True:
            skip_whitespace()
            if position >= len(buffer):
                raise DataQualityError(f"{path}: unexpected end of JSON array")

            if buffer[position] == "]":
                position += 1
                # Confirm that the closing bracket is the final JSON token.
                trailing = buffer[position:] + stream.read()
                if trailing.strip():
                    raise DataQualityError(f"{path}: data found after the JSON array")
                return

            if not first:
                if buffer[position] != ",":
                    raise DataQualityError(
                        f"{path}: expected ',' before array item {item_index}"
                    )
                position += 1
                skip_whitespace()
                if position >= len(buffer):
                    raise DataQualityError(f"{path}: unexpected end before item {item_index}")

            # Keep the start of an incomplete object and add chunks until the
            # decoder either succeeds or the physical file ends.
            while True:
                value_start = position
                try:
                    value, end = decoder.raw_decode(buffer, position)
                except json.JSONDecodeError as exc:
                    if eof:
                        raise DataQualityError(
                            f"{path}: invalid JSON at array item {item_index}: {exc.msg}"
                        ) from exc
                    refill()
                    continue

                # A JSON number can be successfully decoded from a prefix
                # ("2" from a chunk that is followed by "8").  If the value
                # ends exactly at a non-final chunk boundary, retain it and
                # append one more chunk so the delimiter can be observed.
                if end == len(buffer) and not eof:
                    position = value_start
                    refill()
                    continue
                break

            position = end
            yield value
            item_index += 1
            first = False

            # Bound retained memory after small values.  A single unusually
            # large product may temporarily exceed chunk_size, which is the
            # minimum necessary to decode that one value.
            if position >= chunk_size:
                buffer = buffer[position:]
                position = 0


_SCHEMA_KEYWORDS = {
    "$schema",
    "title",
    "description",
    "descrption",  # Typo present in the supplied schema; metadata only.
    "type",
    "properties",
    "required",
    "items",
}


def _assert_supported_schema(schema: Mapping[str, Any], *, path: str = "$") -> None:
    unsupported = set(schema) - _SCHEMA_KEYWORDS
    if unsupported:
        joined = ", ".join(sorted(unsupported))
        raise DataQualityError(f"{path}: unsupported JSON Schema keyword(s): {joined}")
    for name, child in (schema.get("properties") or {}).items():
        _assert_supported_schema(child, path=f"{path}.properties.{name}")
    items = schema.get("items")
    if isinstance(items, Mapping):
        _assert_supported_schema(items, path=f"{path}.items")


def _matches_json_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )
    raise DataQualityError(f"unsupported JSON Schema type: {expected}")


def _validate_schema_value(value: Any, schema: Mapping[str, Any], *, path: str) -> None:
    """Validate the subset used by the supplied Valio JSON Schema.

    The supplied schema uses only ``type``, ``required``, ``properties`` and
    ``items`` (plus descriptive metadata).  Refusing unknown validation
    keywords prevents this small validator from silently claiming broader JSON
    Schema support than it has.
    """

    expected = schema.get("type")
    if expected and not _matches_json_type(value, expected):
        raise DataQualityError(
            f"{path}: expected JSON type {expected}, got {type(value).__name__}"
        )

    if expected == "object":
        for field in schema.get("required") or []:
            if field not in value:
                raise DataQualityError(f"{path}: missing required field {field!r}")
        for field, child_schema in (schema.get("properties") or {}).items():
            if field in value:
                _validate_schema_value(
                    value[field], child_schema, path=f"{path}.{field}"
                )
    elif expected == "array":
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                _validate_schema_value(item, item_schema, path=f"{path}[{index}]")


def _load_product_item_schema(path: Path) -> Mapping[str, Any]:
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            schema = json.load(stream)
    except json.JSONDecodeError as exc:
        raise DataQualityError(f"{path}: invalid JSON Schema: {exc.msg}") from exc
    if not isinstance(schema, dict) or schema.get("type") != "array":
        raise DataQualityError(f"{path}: product schema must describe an array")
    _assert_supported_schema(schema)
    item_schema = schema.get("items")
    if not isinstance(item_schema, dict):
        raise DataQualityError(f"{path}: product schema has no object item schema")
    return item_schema


def _load_metric_csv(
    path: Path,
    *,
    value_column: str,
    minimum: float,
    maximum: float | None,
) -> tuple[dict[str, float | None], int, int]:
    """Load one product-id keyed metric CSV with strict duplicate/range checks."""

    values: dict[str, float | None] = {}
    row_count = 0
    missing_count = 0
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        expected = {"productId", value_column}
        actual = set(reader.fieldnames or [])
        if not expected.issubset(actual):
            raise DataQualityError(
                f"{path}: expected CSV columns {sorted(expected)}, got {sorted(actual)}"
            )
        for line_number, row in enumerate(reader, start=2):
            row_count += 1
            product_id = (row.get("productId") or "").strip()
            if not product_id:
                raise DataQualityError(f"{path}:{line_number}: empty productId")
            if product_id in values:
                raise DataQualityError(
                    f"{path}:{line_number}: duplicate productId {product_id!r}"
                )

            raw_value = (row.get(value_column) or "").strip()
            if not raw_value:
                values[product_id] = None
                missing_count += 1
                continue
            try:
                value = float(raw_value)
            except ValueError as exc:
                raise DataQualityError(
                    f"{path}:{line_number}: invalid {value_column} value {raw_value!r}"
                ) from exc
            if not math.isfinite(value):
                raise DataQualityError(
                    f"{path}:{line_number}: {value_column} must be finite"
                )
            if value < minimum or (maximum is not None and value > maximum):
                upper = f" and <= {maximum}" if maximum is not None else ""
                raise DataQualityError(
                    f"{path}:{line_number}: {value_column} must be >= {minimum}{upper}"
                )
            values[product_id] = value
    return values, row_count, missing_count


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _first_language_value(items: Sequence[Mapping[str, Any]], language: str) -> str | None:
    for item in items:
        if item.get("language") == language:
            value = item.get("value")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _add_flag(flags: dict[str, set[str]], flag: str, detail: str | None = None) -> None:
    details = flags.setdefault(flag, set())
    if detail:
        details.add(detail)


def _positive_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if math.isfinite(number) and number > 0:
            return number
    return None


def _unit_size(
    product: Mapping[str, Any],
    *,
    unit_id: str | None = None,
    gtin: str | None = None,
) -> float | None:
    sizes: set[float] = set()
    for unit in product.get("units") or []:
        if unit_id is not None and unit.get("unitId") != unit_id:
            continue
        if gtin is not None and _normalise_gtin(unit.get("gtin")) != _normalise_gtin(gtin):
            continue
        size = _positive_number(unit.get("sizeInBaseUnits"))
        if size is not None:
            sizes.add(size)
    return next(iter(sizes)) if len(sizes) == 1 else None


def _normalise_gtin(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    # GTINs are sometimes represented with a leading zero at one packaging
    # level.  Numeric comparison is safe here because GTIN is an identifier,
    # not an integer used for arithmetic.
    normalised = value.strip().lstrip("0")
    return normalised or "0"


_VOLUME_FACTORS_L = {
    "LTR": 1.0,
    "L": 1.0,
    "DLT": 0.1,
    "CLT": 0.01,
    "MLT": 0.001,
    "MTQ": 1000.0,
    "M3": 1000.0,
}
_WEIGHT_FACTORS_KG = {"KGM": 1.0, "KG": 1.0, "GRM": 0.001, "MGM": 0.000001}
_LENGTH_FACTORS_M = {"MTR": 1.0, "M": 1.0, "CMT": 0.01, "MMT": 0.001}


def _normalise_measure(measure: Any, factors: Mapping[str, float]) -> float | None:
    if not isinstance(measure, Mapping):
        return None
    value = _positive_number(measure.get("value"))
    factor = factors.get(str(measure.get("unit") or "").upper())
    if value is None or factor is None:
        return None
    return value * factor


def _content_measure(conversion: Mapping[str, Any]) -> tuple[float, str] | None:
    # A few source rows put weight codes in the field named ``volume``.  Treat
    # those as malformed/unknown rather than guessing their meaning.
    volume_l = _normalise_measure(conversion.get("volume"), _VOLUME_FACTORS_L)
    if volume_l is not None:
        return volume_l, "L"
    net_weight_kg = _normalise_measure(conversion.get("netWeight"), _WEIGHT_FACTORS_KG)
    if net_weight_kg is not None:
        return net_weight_kg, "kg"
    return None


def _consistent_measure(
    measures: Sequence[tuple[float, str]],
) -> tuple[float, str] | None:
    if not measures:
        return None
    first_value, first_unit = measures[0]
    for value, unit in measures[1:]:
        if unit != first_unit or not math.isclose(value, first_value, rel_tol=1e-9):
            return None
    return first_value, first_unit


_NAME_UNIT_FACTORS: dict[str, tuple[str, float]] = {
    "l": ("L", 1.0),
    "dl": ("L", 0.1),
    "cl": ("L", 0.01),
    "ml": ("L", 0.001),
    "kg": ("kg", 1.0),
    "g": ("kg", 0.001),
}
_NUMBER = r"(?:\d+(?:[.,]\d+)?)"
_NAME_UNIT = r"(?:kg|ml|cl|dl|l|g)"
_MULTIPACK_RE = re.compile(
    rf"(?P<count>{_NUMBER})\s*(?:x|×|\*)\s*(?P<amount>{_NUMBER})\s*(?P<unit>{_NAME_UNIT})\b",
    re.IGNORECASE,
)
_REVERSE_MULTIPACK_RE = re.compile(
    rf"(?P<amount>{_NUMBER})\s*(?P<unit>{_NAME_UNIT})\s*(?:x|×|\*)\s*(?P<count>{_NUMBER})\b",
    re.IGNORECASE,
)
_SIMPLE_QUANTITY_RE = re.compile(
    rf"(?<![\w.,])(?P<amount>{_NUMBER})\s*(?P<unit>{_NAME_UNIT})\b",
    re.IGNORECASE,
)


def _decimal_number(raw: str) -> float:
    return float(raw.replace(",", "."))


def _quantities_in_name(name: str) -> set[tuple[float, str]]:
    multipacks = list(_MULTIPACK_RE.finditer(name)) + list(
        _REVERSE_MULTIPACK_RE.finditer(name)
    )
    matches: list[tuple[float, str]] = []
    if multipacks:
        for match in multipacks:
            unit, factor = _NAME_UNIT_FACTORS[match.group("unit").lower()]
            value = _decimal_number(match.group("count")) * _decimal_number(
                match.group("amount")
            )
            if value > 0:
                matches.append((value * factor, unit))
    else:
        for match in _SIMPLE_QUANTITY_RE.finditer(name):
            unit, factor = _NAME_UNIT_FACTORS[match.group("unit").lower()]
            value = _decimal_number(match.group("amount"))
            if value > 0:
                matches.append((value * factor, unit))
    return {(round(value, 12), unit) for value, unit in matches}


def _name_quantity(product: Mapping[str, Any]) -> tuple[float, str] | None:
    synkka = product.get("synkkaData") or {}
    parsed: set[tuple[float, str]] = set()
    for item in synkka.get("names") or []:
        value = item.get("value")
        if isinstance(value, str):
            parsed.update(_quantities_in_name(value))
    if len(parsed) != 1:
        return None

    quantity, unit = next(iter(parsed))
    sales_size = _unit_size(product, unit_id=product.get("salesUnit"))
    if sales_size is None:
        if product.get("salesUnit") == product.get("baseUnit"):
            sales_size = 1.0
        else:
            return None

    synkka_gtin = synkka.get("gtin")
    named_size = _unit_size(product, gtin=synkka_gtin) if synkka_gtin else None
    if named_size is None:
        if (
            synkka_gtin
            and _normalise_gtin(synkka_gtin)
            == _normalise_gtin(product.get("salesUnitGtin"))
        ):
            named_size = sales_size
        elif product.get("salesUnit") == product.get("baseUnit"):
            named_size = sales_size
        else:
            # Synkka names normally describe the consumer/base GTIN.  When the
            # GTIN is absent, use the explicit unit hierarchy but keep the
            # weaker NAME_PARSE provenance.
            named_size = 1.0
    factor = sales_size / named_size
    if not math.isfinite(factor) or factor <= 0:
        return None
    return quantity * factor, unit


def _quantity_for_sales_unit(
    product: Mapping[str, Any], flags: dict[str, set[str]]
) -> tuple[float | None, str | None, str]:
    synkka = product.get("synkkaData") or {}
    conversions = synkka.get("unitConversions") or []
    sales_size = _unit_size(product, unit_id=product.get("salesUnit"))
    base_size = _unit_size(product, unit_id=product.get("baseUnit"))

    invalid_units = sorted(
        {
            str(unit.get("unitId") or "?")
            for unit in product.get("units") or []
            if _positive_number(unit.get("sizeInBaseUnits")) is None
        }
    )
    if invalid_units:
        _add_flag(flags, "INVALID_UNIT_SIZE", ",".join(invalid_units))

    if sales_size is None and product.get("salesUnit") == product.get("baseUnit"):
        sales_size = base_size or 1.0
    if base_size is None:
        base_size = 1.0

    if sales_size is not None:
        if product.get("salesUnit") == product.get("baseUnit") or math.isclose(
            sales_size, base_size
        ):
            target_packaging = "BASE_UNIT_OR_EACH"
        elif product.get("salesUnit") == "PAL":
            target_packaging = "PALLET"
        else:
            target_packaging = "CASE"

        direct = [
            measure
            for conversion in conversions
            if conversion.get("synkkaPackagingType") == target_packaging
            if (measure := _content_measure(conversion)) is not None
        ]
        direct_measure = _consistent_measure(direct)
        if direct and direct_measure is None:
            _add_flag(flags, "UNIT_CONVERSION_CONFLICT", target_packaging)
        if direct_measure is not None:
            value, unit = direct_measure
            # A CASE/PALLET conversion is already the total for that packaging
            # level; multiplying it by sizeInBaseUnits would double-count.
            return value, unit, "UNIT_CONVERSIONS"

        base_measures = [
            measure
            for conversion in conversions
            if conversion.get("synkkaPackagingType") == "BASE_UNIT_OR_EACH"
            if (measure := _content_measure(conversion)) is not None
        ]
        base_measure = _consistent_measure(base_measures)
        if base_measures and base_measure is None:
            _add_flag(flags, "UNIT_CONVERSION_CONFLICT", "BASE_UNIT_OR_EACH")
        if base_measure is not None:
            value, unit = base_measure
            synkka_gtin = synkka.get("gtin")
            represented_size = (
                _unit_size(product, gtin=synkka_gtin) if synkka_gtin else None
            )
            if represented_size is None and (
                synkka_gtin
                and _normalise_gtin(synkka_gtin)
                == _normalise_gtin(product.get("salesUnitGtin"))
            ):
                represented_size = sales_size
            if represented_size is None:
                represented_size = (
                    sales_size
                    if product.get("salesUnit") == product.get("baseUnit")
                    else base_size
                )
            return value * (sales_size / represented_size), unit, "UNIT_CONVERSIONS"

        # A dimensional base unit is an explicit structured conversion even if
        # Synkka has no content measure for it.
        if product.get("baseUnit") in {"KG", "KGM"}:
            return sales_size / base_size, "kg", "UNIT_CONVERSIONS"

    name_quantities_present = any(
        _quantities_in_name(str(name.get("value") or ""))
        for name in synkka.get("names") or []
    )
    if str(product.get("category")) in NAME_QUANTITY_CATEGORY_ALLOWLIST:
        parsed = _name_quantity(product)
        if parsed is not None:
            value, unit = parsed
            return value, unit, "NAME_PARSE"
        if name_quantities_present:
            _add_flag(flags, "NAME_QUANTITY_CONFLICT")

    # Piece count is useful only after trying to obtain a physical quantity
    # from the product name.  This preserves 463038 as 20 L rather than 2 pcs.
    if sales_size is not None and product.get("baseUnit") == "ST":
        return sales_size / base_size, "pcs", "UNIT_CONVERSIONS"

    _add_flag(flags, "QUANTITY_UNKNOWN")
    return None, None, "UNKNOWN"


def _case_volume_l(product: Mapping[str, Any]) -> float | None:
    values: list[float] = []
    synkka = product.get("synkkaData") or {}
    for conversion in synkka.get("unitConversions") or []:
        if conversion.get("synkkaPackagingType") != "CASE":
            continue
        dimensions = [
            _normalise_measure(conversion.get(field), _LENGTH_FACTORS_M)
            for field in ("width", "length", "height")
        ]
        if all(value is not None for value in dimensions):
            width, length, height = dimensions
            values.append(width * length * height * 1000.0)
    if not values:
        return None
    if not all(math.isclose(value, values[0], rel_tol=1e-9) for value in values[1:]):
        return None
    return values[0]


_ALLERGEN_PRECEDENCE = {
    "UNDECLARED": 0,
    "FREE_FROM": 1,
    "MAY_CONTAIN": 2,
    "CONTAINS": 3,
}


def _allergen_statuses(
    product: Mapping[str, Any], flags: dict[str, set[str]]
) -> dict[str, str]:
    by_code: dict[str, set[str]] = {}
    classifications = (product.get("synkkaData") or {}).get("classifications") or []
    for group in classifications:
        group_name = group.get("name")
        if group_name not in {"allergen", "nonAllergen"}:
            continue
        for value in group.get("values") or []:
            code = value.get("id")
            status = value.get("unit")
            if not isinstance(code, str) or status not in _ALLERGEN_PRECEDENCE:
                continue
            # nonAllergen is meaningful only as an explicit absence.  Keep an
            # explicit UNDECLARED if supplied, but never reinterpret it as free.
            if group_name == "nonAllergen" and status not in {"FREE_FROM", "UNDECLARED"}:
                continue
            by_code.setdefault(code, set()).add(status)

    resolved: dict[str, str] = {}
    for code, statuses in by_code.items():
        if len(statuses) > 1:
            _add_flag(
                flags,
                "ALLERGEN_CONFLICT",
                f"{code}:{'|'.join(sorted(statuses))}",
            )
        resolved[code] = max(statuses, key=_ALLERGEN_PRECEDENCE.__getitem__)
    return resolved


def _classification_ids(product: Mapping[str, Any], group_name: str) -> set[str]:
    result: set[str] = set()
    for group in (product.get("synkkaData") or {}).get("classifications") or []:
        if group.get("name") != group_name:
            continue
        for value in group.get("values") or []:
            identifier = value.get("id")
            if isinstance(identifier, str) and identifier:
                result.add(identifier)
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _missing_metric(count: int, total: int) -> dict[str, int | float]:
    return {"count": count, "rate": round(count / total, 8) if total else 0.0}


def _make_temp_path(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    return Path(raw_path)


def build_catalog(
    *,
    products_path: Path,
    reliability_path: Path,
    carbon_path: Path,
    output_path: Path,
    report_path: Path,
    schema_path: Path | None = None,
    as_of: date,
) -> dict[str, Any]:
    """Build a catalogue and return the deterministic data-quality report."""

    products_path = Path(products_path)
    reliability_path = Path(reliability_path)
    carbon_path = Path(carbon_path)
    output_path = Path(output_path)
    report_path = Path(report_path)
    schema_path = Path(schema_path) if schema_path is not None else None

    for source in (products_path, reliability_path, carbon_path, SCHEMA_SQL):
        if not source.is_file():
            raise FileNotFoundError(source)
    if schema_path is not None and not schema_path.is_file():
        raise FileNotFoundError(schema_path)
    if output_path.resolve() == report_path.resolve():
        raise ValueError("output_path and report_path must be different files")

    source_paths = {
        "products": products_path,
        "delivery_reliability": reliability_path,
        "carbon": carbon_path,
        "database_schema": SCHEMA_SQL,
    }
    if schema_path is not None:
        source_paths["schema"] = schema_path
    source_hashes = {name: _sha256(path) for name, path in source_paths.items()}

    item_schema = _load_product_item_schema(schema_path) if schema_path else None
    reliability, reliability_rows, reliability_missing = _load_metric_csv(
        reliability_path,
        value_column="deliveryReliability",
        minimum=0.0,
        maximum=1.0,
    )
    carbon, carbon_rows, carbon_missing = _load_metric_csv(
        carbon_path,
        value_column="AVG kg CO2-eq/kg",
        minimum=0.0,
        maximum=None,
    )

    database_temp = _make_temp_path(output_path)
    report_temp: Path | None = None
    connection: sqlite3.Connection | None = None

    seen_ids: set[str] = set()
    categories: set[str] = set()
    substitution_references: set[str] = set()
    deposit_references: set[str] = set()
    missing = Counter()
    qty_sources = Counter()
    flag_counts = Counter()
    conflict_counts = Counter()
    unknown_counts = Counter(
        {
            "quantity": 0,
            "claim_data_products": 0,
            "allergen_data_products": 0,
            "explicit_allergen_rows": 0,
        }
    )
    source_rows = 0

    product_insert = """
        INSERT INTO products (
            id, name_en, name_fi, name_sv, brand, vendor, category,
            sales_unit, base_unit, lot_size, units_json, gtin,
            temperature_condition, min_temp_c, max_temp_c, country,
            qty_per_sales_unit, qty_unit, qty_source, case_volume_l,
            deposits_json, substitutions_json, marketing_text_en,
            has_synkka, synkka_updated_at, delivery_reliability,
            co2_kg_per_kg
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """

    try:
        connection = sqlite3.connect(database_temp)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        connection.execute("BEGIN")

        for index, product in enumerate(iter_json_array(products_path)):
            source_rows += 1
            if item_schema is not None:
                _validate_schema_value(product, item_schema, path=f"$[{index}]")
            if not isinstance(product, dict):
                raise DataQualityError(f"$[{index}]: product must be an object")

            product_id = product.get("id")
            if not isinstance(product_id, str) or not product_id:
                raise DataQualityError(f"$[{index}].id: expected a non-empty string")
            if product_id in seen_ids:
                raise DataQualityError(f"duplicate product id {product_id!r}")
            seen_ids.add(product_id)
            if product.get("deleted") is True:
                raise DataQualityError(
                    f"product {product_id}: deleted rows must not be present in the export"
                )

            try:
                facts = facts_from_raw(product)
            except (KeyError, TypeError, ValueError) as exc:
                raise DataQualityError(
                    f"product {product_id}: cannot extract trust facts: {exc}"
                ) from exc

            flags: dict[str, set[str]] = {}
            synkka = product.get("synkkaData") or {}
            names = synkka.get("names") or []
            name_en_source = _first_language_value(names, "en")
            name_fi = _first_language_value(names, "fi")
            name_sv = _first_language_value(names, "sv")
            if name_en_source is None:
                missing["source_name_en"] += 1
                fallback = name_fi or name_sv
                if fallback is None:
                    fallback = next(
                        (
                            str(item.get("value")).strip()
                            for item in names
                            if isinstance(item.get("value"), str)
                            and str(item.get("value")).strip()
                        ),
                        product_id,
                    )
                name_en = fallback
                fallback_language = "fi" if name_fi else "sv" if name_sv else "other_or_id"
                _add_flag(flags, "NAME_EN_FALLBACK", fallback_language)
            else:
                name_en = name_en_source

            brand = synkka.get("brand")
            if not isinstance(brand, str) or not brand.strip():
                brand = None
                missing["brand"] += 1
            else:
                brand = brand.strip()

            country = product.get("countryOfOrigin")
            if not isinstance(country, str) or not country.strip():
                country = None
                missing["country"] += 1
            else:
                country = country.strip().lower()

            if not facts.has_synkka:
                missing["synkka"] += 1
                _add_flag(flags, "NO_SYNKKA")
            if product.get("temperatureCondition") is None:
                missing["temperature_condition"] += 1
            if product_id not in reliability or reliability.get(product_id) is None:
                missing["delivery_reliability"] += 1
            if product_id not in carbon or carbon.get(product_id) is None:
                missing["carbon"] += 1

            qty_value, qty_unit, qty_source = _quantity_for_sales_unit(product, flags)
            qty_sources[qty_source] += 1
            if qty_value is None:
                missing["quantity"] += 1
                unknown_counts["quantity"] += 1

            structured_claims = _classification_ids(product, "nutritionalClaim")
            facts.structured_claims = structured_claims
            # An empty claim group remains unknown until the business rule is
            # explicitly decided; it is not treated as an explicit negative.
            facts.has_claim_data = bool(structured_claims)
            if not facts.has_claim_data:
                unknown_counts["claim_data_products"] += 1

            allergens = _allergen_statuses(product, flags)
            facts.allergen_list_present = bool(allergens)
            if not facts.allergen_list_present:
                unknown_counts["allergen_data_products"] += 1
            unknown_counts["explicit_allergen_rows"] += sum(
                status == "UNDECLARED" for status in allergens.values()
            )
            for code, status in allergens.items():
                if status in {"CONTAINS", "MAY_CONTAIN", "FREE_FROM"}:
                    facts.allergens[code] = status
                else:
                    facts.allergens.pop(code, None)

            name_conflicts = {
                claim
                for claim in facts.name_claims
                if check_claim(facts, claim, as_of).conflict
            }
            if name_conflicts:
                _add_flag(flags, "NAME_CLAIM_CONFLICT", ",".join(sorted(name_conflicts)))

            labels = _classification_ids(product, "packagingMarkedLabel")
            marketing_text_en = _first_language_value(
                synkka.get("marketingTexts") or [], "en"
            )

            units = product.get("units") or []
            deposits = product.get("deposits") or []
            substitutions: set[str] = set()
            for substitution in product.get("substitutions") or []:
                source_id = substitution.get("product")
                target_id = substitution.get("substituteProduct")
                if source_id not in (None, product_id):
                    _add_flag(
                        flags,
                        "SUBSTITUTION_SOURCE_MISMATCH",
                        str(source_id),
                    )
                if isinstance(target_id, str) and target_id:
                    substitutions.add(target_id)
                    substitution_references.add(target_id)
            for deposit in deposits:
                material = deposit.get("material")
                if isinstance(material, str) and material:
                    deposit_references.add(material)

            category = str(product["category"])
            categories.add(category)
            sales_unit_gtin = product.get("salesUnitGtin")
            if product.get("salesUnitGtin") is None:
                missing["sales_unit_gtin"] += 1

            connection.execute(
                product_insert,
                (
                    product_id,
                    name_en,
                    name_fi,
                    name_sv,
                    brand,
                    str(product["vendorName"]),
                    category,
                    str(product["salesUnit"]),
                    str(product["baseUnit"]),
                    float(product["allowedLotSize"]),
                    _canonical_json(units),
                    sales_unit_gtin,
                    product.get("temperatureCondition"),
                    synkka.get("minTemperature"),
                    synkka.get("maxTemperature"),
                    country,
                    qty_value,
                    qty_unit,
                    qty_source,
                    _case_volume_l(product),
                    _canonical_json(deposits),
                    _canonical_json(sorted(substitutions)),
                    marketing_text_en,
                    int(facts.has_synkka),
                    synkka.get("lastUpdated"),
                    reliability.get(product_id),
                    carbon.get(product_id),
                ),
            )
            connection.execute(
                "INSERT INTO products_fts (id,name_en,name_fi,name_sv,brand) VALUES (?,?,?,?,?)",
                (product_id, name_en, name_fi, name_sv, brand),
            )

            for claim in sorted(structured_claims | facts.name_claims):
                source = "STRUCTURED" if claim in structured_claims else "NAME_ONLY"
                connection.execute(
                    "INSERT INTO product_claims (product_id,claim,source) VALUES (?,?,?)",
                    (product_id, claim, source),
                )
            for code, status in sorted(allergens.items()):
                grade = check_allergen_free(facts, code, as_of).grade
                connection.execute(
                    """INSERT INTO product_allergens
                       (product_id,code,status,trust_grade) VALUES (?,?,?,?)""",
                    (product_id, code, status, grade),
                )
            for label in sorted(labels):
                connection.execute(
                    "INSERT INTO product_labels (product_id,label) VALUES (?,?)",
                    (product_id, label),
                )
            for flag, details in sorted(flags.items()):
                detail = ";".join(sorted(details)) or None
                connection.execute(
                    "INSERT INTO product_flags (product_id,flag,detail) VALUES (?,?,?)",
                    (product_id, flag, detail),
                )
                flag_counts[flag] += 1
                if flag in {
                    "ALLERGEN_CONFLICT",
                    "NAME_CLAIM_CONFLICT",
                    "NAME_QUANTITY_CONFLICT",
                    "UNIT_CONVERSION_CONFLICT",
                }:
                    conflict_counts[flag] += 1

        connection.executemany(
            "INSERT INTO categories (code,name_en,name_fi) VALUES (?,NULL,NULL)",
            ((code,) for code in sorted(categories)),
        )
        connection.commit()

        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if foreign_key_errors:
            raise DataQualityError(
                f"generated database has {len(foreign_key_errors)} foreign-key violation(s)"
            )
        if integrity != "ok":
            raise DataQualityError(f"generated database failed integrity_check: {integrity}")

        table_rows = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in STATIC_TABLES
        }
        connection.close()
        connection = None

        reliability_orphans = sorted(set(reliability) - seen_ids)
        carbon_orphans = sorted(set(carbon) - seen_ids)
        substitution_orphans = sorted(substitution_references - seen_ids)
        # Deposit material codes are separately priced external catalogue items
        # in the supplied data, not broken product foreign keys.
        external_deposit_materials = sorted(deposit_references - seen_ids)

        # The report must describe the exact bytes used for the build.  Detect
        # a source file being replaced while preprocessing rather than hashing
        # a different generation after the fact.
        for source_name, source_path in source_paths.items():
            if _sha256(source_path) != source_hashes[source_name]:
                raise DataQualityError(
                    f"{source_path}: source changed while the catalogue was being built"
                )

        report: dict[str, Any] = {
            "version": 1,
            "as_of": as_of.isoformat(),
            "inputs": {
                "products": {
                    "name": products_path.name,
                    "sha256": source_hashes["products"],
                },
                "delivery_reliability": {
                    "name": reliability_path.name,
                    "sha256": source_hashes["delivery_reliability"],
                },
                "carbon": {
                    "name": carbon_path.name,
                    "sha256": source_hashes["carbon"],
                },
                "schema": (
                    {"name": schema_path.name, "sha256": source_hashes["schema"]}
                    if schema_path
                    else None
                ),
                "database_schema": {
                    "name": SCHEMA_SQL.name,
                    "sha256": source_hashes["database_schema"],
                },
            },
            "output": {"database_sha256": _sha256(database_temp)},
            "source_rows": {
                "products": source_rows,
                "delivery_reliability": reliability_rows,
                "carbon": carbon_rows,
            },
            "counts": {
                "products": table_rows["products"],
                "categories": table_rows["categories"],
                "claims": table_rows["product_claims"],
                "allergens": table_rows["product_allergens"],
                "labels": table_rows["product_labels"],
                "flags": table_rows["product_flags"],
            },
            "table_rows": table_rows,
            "uniqueness": {
                "product_ids": len(seen_ids),
                "duplicate_product_ids": 0,
                "duplicate_delivery_ids": 0,
                "duplicate_carbon_ids": 0,
            },
            "missing": {
                key: _missing_metric(missing[key], source_rows)
                for key in (
                    "source_name_en",
                    "brand",
                    "country",
                    "synkka",
                    "temperature_condition",
                    "sales_unit_gtin",
                    "delivery_reliability",
                    "carbon",
                    "quantity",
                )
            },
            "csv_missing_values": {
                "delivery_reliability": reliability_missing,
                "carbon": carbon_missing,
            },
            "quantity_sources": dict(sorted(qty_sources.items())),
            "unknown": dict(sorted(unknown_counts.items())),
            "flags": dict(sorted(flag_counts.items())),
            "conflicts": dict(sorted(conflict_counts.items())),
            "orphans": {
                "delivery_reliability": {
                    "count": len(reliability_orphans),
                    "ids": reliability_orphans,
                },
                "carbon": {"count": len(carbon_orphans), "ids": carbon_orphans},
                "substitutions": {
                    "count": len(substitution_orphans),
                    "ids": substitution_orphans,
                },
            },
            "external_references": {
                "deposit_materials": {
                    "count": len(external_deposit_materials),
                    "ids": external_deposit_materials,
                }
            },
            "checks": {
                "schema_errors": 0 if schema_path else None,
                "foreign_key_check": "ok",
                "integrity_check": "ok",
            },
        }

        report_temp = _make_temp_path(report_path)
        with report_temp.open("w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(database_temp, output_path)
        os.replace(report_temp, report_path)
        report_temp = None
        return report
    except sqlite3.Error as exc:
        raise DataQualityError(f"SQLite catalogue build failed: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()
        if database_temp.exists():
            database_temp.unlink()
        if report_temp is not None and report_temp.exists():
            report_temp.unlink()


def _default_source_dir() -> Path:
    candidates = (PROJECT_ROOT / "data", PROJECT_ROOT.parent / "docs_from_valio")
    for candidate in candidates:
        if (candidate / PRODUCTS_FILENAME).is_file():
            return candidate
    return candidates[0]


def _parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build backend/data/products.sqlite from the Valio challenge exports."
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=_default_source_dir(),
        help="directory containing the three Valio export files",
    )
    parser.add_argument("--products", type=Path, help="override the product JSON path")
    parser.add_argument(
        "--reliability", type=Path, help="override the delivery reliability CSV path"
    )
    parser.add_argument("--carbon", type=Path, help="override the carbon CSV path")
    parser.add_argument("--schema", type=Path, help="override the product JSON Schema path")
    parser.add_argument(
        "--skip-schema-validation",
        action="store_true",
        help="skip per-product validation against the supplied JSON Schema",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=BACKEND_ROOT / "data" / "products.sqlite",
        help="SQLite destination (atomically replaced)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="quality-report destination (default: <output stem>.quality.json)",
    )
    parser.add_argument(
        "--as-of",
        type=_parse_date,
        default=date.today(),
        help="date used for trust freshness grades (YYYY-MM-DD)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    source_dir = args.source_dir
    products_path = args.products or source_dir / PRODUCTS_FILENAME
    reliability_path = args.reliability or source_dir / RELIABILITY_FILENAME
    carbon_path = args.carbon or source_dir / CARBON_FILENAME
    schema_path = None
    if not args.skip_schema_validation:
        schema_path = args.schema or source_dir / PRODUCT_SCHEMA_FILENAME
    output_path = args.output
    report_path = args.report or output_path.with_suffix(".quality.json")

    try:
        report = build_catalog(
            products_path=products_path,
            reliability_path=reliability_path,
            carbon_path=carbon_path,
            output_path=output_path,
            report_path=report_path,
            schema_path=schema_path,
            as_of=args.as_of,
        )
    except (DataQualityError, FileNotFoundError, OSError, ValueError) as exc:
        print(f"preprocess failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"built {report['table_rows']['products']} products in {output_path}\n"
        f"quality report: {report_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
