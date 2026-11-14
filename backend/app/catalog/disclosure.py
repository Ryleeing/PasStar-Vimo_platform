"""Filter structured payloads through an explicit, default-deny field policy.

Policy containers expand only to known model fields. A container never grants
access to arbitrary descendants. Resource ownership and protocol authorization
must be checked by the caller before invoking this field-level filter.
"""
from __future__ import annotations

import math
import sqlite3
import threading
import types
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Union, get_args, get_origin

import yaml
from pydantic import BaseModel

from .. import schemas
from ..internal import Audience, DisclosablePack
from ..schemas import (
    Candidate, ChainEvent, Order, ProductPublic, Profile, Quote, StructuredRequest, Totals,
)


DEFAULT_POLICY = Path(__file__).resolve().parents[1] / "config" / "field_policy.yaml"
DEFAULT_RUNTIME_DATABASE = Path(__file__).resolve().parents[2] / "data" / "runtime.sqlite"
_RANK = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}
_KINDS = {"AGENT", "BUYER_USER", "VALIO_USER", "LLM"}
_DROP = object()
# These reviewed fields are deliberately independent of model_fields. New API
# fields or new models remain private until this manifest is explicitly updated.
_PUBLIC_MODEL_FIELDS = {
    schemas.AllergenCard: ("rows", "data_updated_at", "notes"),
    schemas.AllergenRow: ("product_id", "requirement", "status", "trust_grade", "source"),
    schemas.AllergenStatus: ("code", "label", "status", "trust_grade"),
    schemas.AlternativeRef: ("candidate_id", "tradeoff", "tier"),
    schemas.AuthorizationRequest: (
        "protocol_id", "buyer_id", "request_id", "quote_id", "amount_gross_cents",
        "currency", "delivery_date", "quote_valid_until", "reason_codes", "reasons",
        "approver", "sign_with_kid",
    ),
    schemas.Candidate: (
        "id", "rank", "badge", "selectable", "lines", "totals", "delivery_date",
        "reliability_level", "outcome_preview", "outcome_reasons", "cards", "violations",
        "explanation",
    ),
    schemas.CandidateLine: (
        "product_id", "name", "brand", "sales_unit", "sales_units", "qty_covered",
        "overbuy", "conversion_source", "unit_price_cents", "line_net_cents", "vat_rate",
        "deposit_cents", "available_on_date", "deliverable_sales_units",
        "substitution_tier", "substitutes_for",
    ),
    schemas.Cards: ("allergen", "trust", "alternatives", "tips"),
    schemas.ChainEvent: ("at", "title", "logic", "evidence"),
    schemas.EvidenceRef: ("source", "detail", "trust_grade"),
    schemas.GateDecision: ("outcome", "rules_hit", "summary"),
    schemas.HardExclusions: ("brands", "vendors", "product_ids", "requirements"),
    schemas.Order: (
        "id", "request_id", "quote_id", "status", "gate", "approval_id", "requote_reason",
        "new_quote_id", "authorization_request", "authorized_by", "created_at", "placed_at",
    ),
    schemas.ProductPublic: (
        "id", "name", "brand", "category", "sales_unit", "qty_per_sales_unit", "qty_unit",
        "claims", "labels", "allergens", "country", "has_manufacturer_data", "data_updated_at",
        "flags",
    ),
    schemas.Profile: ("id", "buyer_id", "body", "updated_at"),
    schemas.ProfileBody: ("name", "hard_exclusions", "soft_preferences", "defaults", "weights"),
    schemas.ProfileDefaults: ("delivery_address", "lead_days", "substitution"),
    schemas.Quantity: ("value", "unit"),
    schemas.Quote: (
        "id", "request_id", "candidate_id", "protocol_id", "lines", "totals", "delivery_date",
        "valid_until", "status", "jws",
    ),
    schemas.RankingWeights: ("price", "fulfilment", "carbon", "local"),
    schemas.Requirement: ("kind", "code", "label"),
    schemas.RuleHit: ("rule_id", "description", "outcome"),
    schemas.SoftPreferences: ("labels", "countries", "prefer_large_packs"),
    schemas.StructuredRequest: (
        "category", "category_label", "requirements", "quantity", "delivery_date",
        "budget_cents", "substitution", "notes",
    ),
    schemas.Tip: ("kind", "text"),
    schemas.Totals: ("net_cents", "vat_cents", "deposit_cents", "gross_cents"),
    schemas.TrustScore: ("evidence", "match", "fulfilment", "compliance", "overall", "how_to_improve", "points"),
    schemas.TrustPoints: ("evidence", "match", "fulfilment", "compliance", "overall"),
    schemas.Violation: ("rule", "detail", "product_id"),
}
_CREATE_AUDIT_TABLE = """
CREATE TABLE IF NOT EXISTS disclosures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    request_id TEXT,
    product_id TEXT,
    field TEXT NOT NULL,
    sensitivity TEXT NOT NULL CHECK (sensitivity IN ('L0','L1','L2','L3'))
)
"""


@dataclass
class _Shape:
    kind: str
    annotation: Any = None
    children: dict[str, "_Shape"] = field(default_factory=dict)
    item: "_Shape | None" = None
    nullable: bool = False


def _shape(annotation: Any) -> _Shape:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in {Union, types.UnionType}:
        values = [value for value in args if value is not type(None)]
        if len(values) != 1:
            return _Shape("denied")
        result = _shape(values[0])
        result.nullable = type(None) in args
        return result
    if origin is list:
        return _Shape("list", item=_shape(args[0]))
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        approved = _PUBLIC_MODEL_FIELDS.get(annotation)
        if approved is None:
            return _Shape("denied")
        return _Shape(
            "object", annotation=annotation,
            children={
                name: _shape(annotation.model_fields[name].annotation)
                for name in approved if name in annotation.model_fields
            },
        )
    if annotation in {str, int, float, bool, date, datetime} or origin is Literal:
        return _Shape("scalar", annotation=annotation)
    # Any, untyped dictionaries and unknown custom objects never open a path.
    return _Shape("denied")


def _object(**fields: Any) -> _Shape:
    return _Shape("object", children={name: _shape(value) for name, value in fields.items()})


def _roots() -> dict[str, _Shape]:
    return {
        "product": _shape(ProductPublic),
        "price": _object(unit_price_cents=int, vat_rate=float, deposit_cents=int),
        "availability": _object(
            status=Literal["YES", "PARTIAL", "NO", "UNKNOWN"],
            deliverable_sales_units=float | None,
        ),
        "totals": _shape(Totals),
        "order": _shape(Order),
        "quote": _shape(Quote),
        "profile": _shape(Profile),
        "candidate": _shape(Candidate),
        "candidates": _shape(list[Candidate]),
        "blocked": _shape(list[Candidate]),
        "request": _shape(StructuredRequest),
        "events": _shape(list[ChainEvent]),
        "reliability_level": _shape(Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]),
        "unconvertible_product_ids": _shape(list[str]),
        "no_candidate_reason": _shape(str | None),
        "dropped_count": _shape(int),
        "stock": _object(quantity=float),
        "delivery_reliability": _object(raw=float),
        "supplier": _object(performance=float),
    }


def _resolve(roots: dict[str, _Shape], path: str) -> _Shape | None:
    parts = path.split(".")
    current = roots.get(parts[0])
    for part in parts[1:]:
        while current is not None and current.kind == "list":
            current = current.item
        if current is None or current.kind != "object":
            return None
        current = current.children.get(part)
    return current


def _leaf_paths(shape: _Shape, path: str):
    if shape.kind == "scalar":
        yield path
    elif shape.kind == "list":
        yield from _leaf_paths(shape.item, path)
    elif shape.kind == "object":
        for name, child in shape.children.items():
            yield from _leaf_paths(child, f"{path}.{name}")


def _scalar(value: Any, annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is Literal:
        return value if any(type(value) is type(item) and value == item for item in get_args(annotation)) else _DROP
    if annotation is str:
        return value if isinstance(value, str) else _DROP
    if annotation is bool:
        return value if isinstance(value, bool) else _DROP
    if annotation is int:
        return value if type(value) is int else _DROP
    if annotation is float:
        return value if type(value) in {int, float} and math.isfinite(value) else _DROP
    if annotation in {date, datetime}:
        if isinstance(value, annotation):
            return value.isoformat()
        if isinstance(value, str):
            try:
                annotation.fromisoformat(value)
            except ValueError:
                return _DROP
            return value
    return _DROP


class DisclosureFilter:
    """Apply the configured policy and audit only successfully disclosed fields.

    All ordinary audiences, including LLMs, are capped at L1. Explicitly opted-in
    Valio internal readers may receive listed L2 fields, but L3 never leaves this
    boundary. Caller-owned SQLite connections remain caller-owned.
    """

    def __init__(
        self, policy_path: str | Path = DEFAULT_POLICY, *,
        db_path: str | Path = DEFAULT_RUNTIME_DATABASE,
        connection: sqlite3.Connection | None = None,
        allow_internal_l2: bool = False,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.policy_path = Path(policy_path)
        self.db_path = Path(db_path)
        self.connection = connection
        self.allow_internal_l2 = allow_internal_l2
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._roots = _roots()
        self._declared: dict[str, str] = {}
        self._leaves: dict[str, str] = {}
        with self.policy_path.open("r", encoding="utf-8") as stream:
            policy = yaml.safe_load(stream)
        if not isinstance(policy, dict) or set(policy) != set(_RANK):
            raise ValueError("field policy must contain exactly L0, L1, L2 and L3")
        for level, entries in policy.items():
            if not isinstance(entries, list) or any(not isinstance(path, str) for path in entries):
                raise ValueError("field policy entries must be lists of paths")
            for path in entries:
                shape = _resolve(self._roots, path)
                if shape is None:
                    # L3 deny declarations may name private structures that this
                    # public schema intentionally does not know how to serialize.
                    if level == "L3":
                        continue
                    raise ValueError(f"unknown public field policy path: {path}")
                old = self._declared.get(path, "L0")
                self._declared[path] = max((old, level), key=_RANK.__getitem__)
                for leaf in _leaf_paths(shape, path):
                    old = self._leaves.get(leaf, "L0")
                    self._leaves[leaf] = max((old, level), key=_RANK.__getitem__)

    def _ceiling(self, audience: Audience) -> int:
        if audience.kind not in _KINDS or audience.ceiling not in _RANK:
            raise ValueError("invalid audience kind or sensitivity ceiling")
        maximum = 2 if audience.kind == "VALIO_USER" and self.allow_internal_l2 else 1
        return min(maximum, _RANK[audience.ceiling])

    def _level(self, path: str, ceiling: int) -> str | None:
        level = self._leaves.get(path, self._declared.get(path))
        return level if level is not None and _RANK[level] <= ceiling else None

    def _has_allowed_descendant(self, path: str, ceiling: int) -> bool:
        prefix = path + "."
        return any(
            (leaf == path or leaf.startswith(prefix)) and _RANK[level] <= ceiling
            for leaf, level in self._leaves.items()
        )

    def _walk(self, value, shape, path, ceiling, fields, product_id):
        level = self._level(path, ceiling)
        if not self._has_allowed_descendant(path, ceiling):
            return _DROP
        if value is None:
            if shape.nullable:
                if level is not None:
                    fields.add((path, level, product_id))
                return None
            return _DROP
        if shape.kind == "scalar":
            if level is None:
                return _DROP
            result = _scalar(value, shape.annotation)
            if result is not _DROP:
                fields.add((path, level, product_id))
            return result
        if shape.kind == "list":
            if not isinstance(value, list):
                return _DROP
            result = []
            for item in value:
                kept = self._walk(item, shape.item, path, ceiling, fields, product_id)
                if kept is not _DROP:
                    result.append(kept)
            if not result and level is not None:
                fields.add((path, level, product_id))
            return result
        if shape.kind == "object":
            if not isinstance(value, dict):
                return _DROP
            if (
                shape.annotation is ProductPublic and isinstance(value.get("id"), str)
                and self._level(f"{path}.id", ceiling) is not None
            ):
                product_id = value["id"]
            elif (
                "product_id" in shape.children and isinstance(value.get("product_id"), str)
                and self._level(f"{path}.product_id", ceiling) is not None
            ):
                product_id = value["product_id"]
            result = {}
            for name, child in shape.children.items():
                if name in value:
                    kept = self._walk(
                        value[name], child, f"{path}.{name}", ceiling, fields, product_id
                    )
                    if kept is not _DROP:
                        result[name] = kept
            if not result and level is not None:
                fields.add((path, level, product_id))
            return result
        return _DROP

    def _filtered(self, obj: dict, audience: Audience):
        if not isinstance(obj, dict):
            raise TypeError("disclosure input must be a canonical dictionary envelope")
        ceiling = self._ceiling(audience)
        fields: set[tuple[str, str, str | None]] = set()
        result = {}
        for name, shape in self._roots.items():
            if name in obj:
                kept = self._walk(obj[name], shape, name, ceiling, fields, None)
                if kept is not _DROP:
                    result[name] = kept
        return result, fields

    def filter(
        self, obj: dict, audience: Audience, *, request_id: str | None = None,
    ) -> dict:
        result, fields = self._filtered(obj, audience)
        self.record(audience, request_id, sorted(fields, key=lambda item: (item[0], item[2] or "")))
        return result

    def filter_product(
        self, product: dict, audience: Audience, *, request_id: str | None = None,
    ) -> dict:
        """Wrap a ProductPublic dictionary explicitly, without guessing its root type."""
        return self.filter({"product": product}, audience, request_id=request_id).get("product", {})

    def record(
        self, audience: Audience, request_id: str | None,
        fields: list[tuple[str, str, str | None]],
    ) -> None:
        """Persist field metadata only; invalid or failed audit writes raise."""
        ceiling = self._ceiling(audience)
        rows = []
        at = self.now()
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        actor = audience.agent_id or f"{audience.kind}:{audience.buyer_id or '-'}"
        for path, level, product_id in fields:
            if self._level(path, ceiling) != level:
                raise ValueError("audit field is outside the configured disclosure scope")
            rows.append((at.isoformat(), actor, request_id, product_id, path, level))
        if not rows:
            return
        with self._lock:
            owned = self.connection is None
            if owned:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = self.connection or sqlite3.connect(self.db_path)
            try:
                with connection:
                    connection.execute(_CREATE_AUDIT_TABLE)
                    connection.executemany(
                        """INSERT INTO disclosures
                        (at,agent_id,request_id,product_id,field,sensitivity)
                        VALUES (?,?,?,?,?,?)""", rows,
                    )
            finally:
                if owned:
                    connection.close()

    def pack_for_llm(
        self, obj: dict, audience: Audience, *, request_id: str | None = None,
    ) -> DisclosablePack:
        """Always enforce LLM limits, even when given an internal-reader audience."""
        llm = replace(audience, kind="LLM")
        result, fields = self._filtered(obj, llm)
        self.record(llm, request_id, sorted(fields, key=lambda item: (item[0], item[2] or "")))
        maximum = max((level for _, level, _ in fields), key=_RANK.__getitem__, default="L0")
        return DisclosablePack(data=result, max_sensitivity=maximum)

