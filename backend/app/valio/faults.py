"""Deterministic fault injection around the normalized Valio client interface.

Faults are disabled by default. A store can remain in memory for a test or use
the existing SQLite ``faults`` table for a repeatable local demo. Configuration
is validated before publication, and returned models never share mutable state
with the store or the upstream client's cache.
"""
from __future__ import annotations

import copy
import json
import math
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP, localcontext
from pathlib import Path
from typing import Iterator, Literal, Mapping, Sequence

from ..internal import AvailabilityInfo, PriceInfo, ValioApi
from ..schemas import Fault, FaultUpdate
from .client import ValioClientError


FaultName = Literal["PRICE_CHANGE", "PARTIAL_STOCK", "TIMEOUT", "RATE_LIMIT", "EMPTY_RESULT"]
FAULT_NAMES: tuple[FaultName, ...] = (
    "PRICE_CHANGE", "PARTIAL_STOCK", "TIMEOUT", "RATE_LIMIT", "EMPTY_RESULT",
)
_ENDPOINTS = {"prices", "availability", "both"}
_COMMON_PARAMS = {"product_ids", "endpoint", "times"}
_EXTRA_PARAMS = {
    "PRICE_CHANGE": {"multiplier", "delta_cents"},
    "PARTIAL_STOCK": {"ratio"},
    "TIMEOUT": set(),
    "RATE_LIMIT": {"retry_after"},
    "EMPTY_RESULT": set(),
}


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _validate_name(name: str) -> None:
    if name not in FAULT_NAMES:
        raise ValueError("unknown fault name")


def _validate_params(name: str, params: Mapping) -> dict:
    if not isinstance(params, Mapping):
        raise ValueError("fault params must be an object")
    unknown = set(params) - _COMMON_PARAMS - _EXTRA_PARAMS[name]
    if unknown:
        raise ValueError("unsupported fault parameter")
    result = copy.deepcopy(dict(params))

    if "product_ids" in result:
        targets = result["product_ids"]
        if (
            not isinstance(targets, list)
            or not targets
            or any(not isinstance(pid, str) or not pid or pid != pid.strip() for pid in targets)
            or len(set(targets)) != len(targets)
        ):
            raise ValueError("product_ids must be a non-empty list of unique product codes")
    if result.get("endpoint", "both") not in _ENDPOINTS:
        raise ValueError("endpoint must be prices, availability or both")
    if "times" in result:
        times = result["times"]
        if not isinstance(times, int) or isinstance(times, bool) or times <= 0:
            raise ValueError("times must be a positive integer")

    if name == "PRICE_CHANGE":
        if "multiplier" in result and "delta_cents" in result:
            raise ValueError("choose either multiplier or delta_cents")
        if "multiplier" in result and _number(result["multiplier"], "multiplier") <= 0:
            raise ValueError("multiplier must be positive")
        if "delta_cents" in result:
            delta = result["delta_cents"]
            if not isinstance(delta, int) or isinstance(delta, bool):
                raise ValueError("delta_cents must be an integer")
    if name == "PARTIAL_STOCK" and not 0 <= _number(result.get("ratio", .5), "ratio") < 1:
        raise ValueError("ratio must be at least zero and less than one")
    if name == "RATE_LIMIT" and _number(result.get("retry_after", 1), "retry_after") < 0:
        raise ValueError("retry_after must be non-negative")
    return result


class FaultStore:
    """Thread-safe configuration and finite trigger budgets.

    ``times`` is the remaining matching trigger budget. Once exhausted, the
    fault is disabled and its reported remaining budget is zero. Setting a
    configuration again establishes a fresh budget. SQLite updates use an
    immediate transaction so independent store instances consume atomically.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self._closed = False
        self._memory = {
            name: Fault(name=name, enabled=False, params={}) for name in FAULT_NAMES
        }
        if db_path is not None:
            if str(db_path) != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(
                str(db_path), timeout=10, isolation_level=None, check_same_thread=False,
            )
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS faults (
                    name TEXT PRIMARY KEY CHECK (
                        name IN ('PRICE_CHANGE','PARTIAL_STOCK','TIMEOUT','RATE_LIMIT','EMPTY_RESULT')
                    ),
                    enabled INTEGER NOT NULL DEFAULT 0,
                    params_json TEXT NOT NULL DEFAULT '{}'
                )
            """)
            with self._transaction():
                self._connection.executemany(
                    "INSERT OR IGNORE INTO faults (name,enabled,params_json) VALUES (?,0,'{}')",
                    ((name,) for name in FAULT_NAMES),
                )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            self._assert_open()
            connection = self._connection
            if connection is not None:
                connection.execute("BEGIN IMMEDIATE")
            try:
                yield
                if connection is not None:
                    connection.execute("COMMIT")
            except BaseException:
                if connection is not None:
                    connection.execute("ROLLBACK")
                raise

    def _assert_open(self) -> None:
        if self._closed:
            raise RuntimeError("fault store is closed")

    def _read(self, name: str) -> Fault:
        self._assert_open()
        if self._connection is None:
            return self._memory[name].model_copy(deep=True)
        row = self._connection.execute(
            "SELECT enabled,params_json FROM faults WHERE name = ?", (name,),
        ).fetchone()
        if row is None:
            raise ValueError("fault configuration is missing")
        return Fault(name=name, enabled=bool(row[0]), params=json.loads(row[1]))

    def _write(self, fault: Fault) -> None:
        if self._connection is None:
            self._memory[fault.name] = fault.model_copy(deep=True)
        else:
            self._connection.execute(
                "UPDATE faults SET enabled = ?, params_json = ? WHERE name = ?",
                (int(fault.enabled), json.dumps(fault.params, sort_keys=True), fault.name),
            )

    def get(self, name: str) -> Fault:
        _validate_name(name)
        with self._lock:
            return self._read(name)

    def list(self) -> list[Fault]:
        with self._lock:
            return [self._read(name) for name in FAULT_NAMES]

    def set(
        self, name: str, update: FaultUpdate | None = None, *,
        enabled: bool | None = None, params: Mapping | None = None,
    ) -> Fault:
        """Replace one configuration, rejecting invalid changes atomically."""
        _validate_name(name)
        if update is not None:
            if not isinstance(update, FaultUpdate) or enabled is not None or params is not None:
                raise ValueError("provide either FaultUpdate or enabled/params")
            enabled, params = update.enabled, update.params
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        validated = _validate_params(name, {} if params is None else params)
        fault = Fault(name=name, enabled=enabled, params=validated)
        with self._transaction():
            self._write(fault)
        return fault.model_copy(deep=True)

    def reset(self) -> list[Fault]:
        with self._transaction():
            for name in FAULT_NAMES:
                self._write(Fault(name=name, enabled=False, params={}))
        return self.list()

    def take(self, name: str, endpoint: str, product_ids: Sequence[str]) -> Fault | None:
        """Consume one trigger only when enabled, endpoint and targets match."""
        _validate_name(name)
        if endpoint not in {"prices", "availability"}:
            raise ValueError("endpoint must be prices or availability")
        with self._transaction():
            fault = self._read(name)
            if not fault.enabled or fault.params.get("endpoint", "both") not in {endpoint, "both"}:
                return None
            targets = set(fault.params.get("product_ids", product_ids))
            if not targets.intersection(product_ids):
                return None
            remaining = fault.params.get("times")
            if remaining is not None:
                if not isinstance(remaining, int) or isinstance(remaining, bool) or remaining <= 0:
                    return None
                replacement = fault.model_copy(deep=True)
                replacement.params["times"] = remaining - 1
                replacement.enabled = remaining > 1
                self._write(replacement)
            return fault

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            self._closed = True

    def __enter__(self) -> FaultStore:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def _ids(product_ids: Sequence[str]) -> list[str]:
    if (
        not isinstance(product_ids, (list, tuple))
        or len(product_ids) > 8
        or any(not isinstance(pid, str) or not pid or pid != pid.strip() for pid in product_ids)
        or len(set(product_ids)) != len(product_ids)
    ):
        raise ValueError("request must contain at most eight unique non-empty product codes")
    return list(product_ids)


def _date(value: date, name: str) -> None:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError(f"{name} must be a date")


def _target_ids(fault: Fault, ids: Sequence[str]) -> set[str]:
    return set(fault.params.get("product_ids", ids)).intersection(ids)


class FaultProxy:
    """Inject faults without mutating upstream data or making unnecessary calls.

    TIMEOUT takes precedence over RATE_LIMIT, which takes precedence over
    EMPTY_RESULT. A higher short-circuit fault does not consume lower budgets.
    EMPTY_RESULT removes matching products before calling upstream; remaining
    products may still receive PRICE_CHANGE or PARTIAL_STOCK. Transform faults
    consume their budgets only after a successful upstream result contains a
    matching product. PARTIAL_STOCK additionally requires an available product
    with a positive known partial amount, or a full YES response. Fault controls
    must be exposed only to trusted demo tools.
    """

    def __init__(self, upstream: ValioApi, store: FaultStore | None = None) -> None:
        self.upstream = upstream
        self.store = store if store is not None else FaultStore()

    @property
    def buyer_id(self) -> str | None:
        return getattr(self.upstream, "buyer_id", None)

    def clear_cache(self) -> None:
        """Forward fresh-quote cache invalidation without changing fault state."""
        clear = getattr(self.upstream, "clear_cache", None)
        if callable(clear):
            clear()

    def _before(self, endpoint: str, ids: list[str]) -> list[str]:
        if self.store.take("TIMEOUT", endpoint, ids) is not None:
            raise ValioClientError(
                "UPSTREAM_ERROR", "Simulated Valio timeout", retryable=True,
            )
        limited = self.store.take("RATE_LIMIT", endpoint, ids)
        if limited is not None:
            raise ValioClientError(
                "RATE_LIMITED", "Simulated Valio rate limit", status_code=429,
                retryable=True, retry_after=float(limited.params.get("retry_after", 1)),
            )
        empty = self.store.take("EMPTY_RESULT", endpoint, ids)
        if empty is None:
            return ids
        removed = _target_ids(empty, ids)
        return [pid for pid in ids if pid not in removed]

    def prices(
        self, product_ids: list[str], buyer_id: str, *, delivery_date: date | None = None,
    ) -> dict[str, PriceInfo]:
        ids = _ids(product_ids)
        if not isinstance(buyer_id, str) or not buyer_id.strip():
            raise ValueError("buyer_id is required")
        if self.buyer_id is not None and buyer_id != self.buyer_id:
            raise ValioClientError("OUT_OF_SCOPE", "Buyer does not match the configured customer")
        if delivery_date is not None:
            _date(delivery_date, "delivery_date")
        remaining = self._before("prices", ids)
        if not remaining:
            return {}
        if delivery_date is None:
            upstream = self.upstream.prices(remaining, buyer_id)
        else:
            upstream = self.upstream.prices(remaining, buyer_id, delivery_date=delivery_date)
        result = {pid: copy.deepcopy(upstream[pid]) for pid in remaining if pid in upstream}
        changed = self.store.take("PRICE_CHANGE", "prices", list(result))
        if changed is not None:
            targets = _target_ids(changed, result)
            for pid in targets:
                old = result[pid]
                if "delta_cents" in changed.params:
                    adjusted = old.unit_price_cents + changed.params["delta_cents"]
                else:
                    multiplier = Decimal(str(changed.params.get("multiplier", 1.1)))
                    with localcontext() as context:
                        context.prec = max(
                            28, len(str(abs(old.unit_price_cents)))
                            + len(multiplier.as_tuple().digits)
                            + abs(multiplier.as_tuple().exponent) + 8,
                        )
                        adjusted = int((Decimal(old.unit_price_cents) * multiplier).quantize(
                            Decimal("1"), rounding=ROUND_HALF_UP,
                        ))
                if adjusted < 0:
                    raise ValioClientError(
                        "VALIDATION_ERROR", "Simulated price adjustment would produce a negative price",
                    )
                gross = None
                if old.unit_gross_cents is not None:
                    rate = Decimal(str(old.vat_rate))
                    with localcontext() as context:
                        context.prec = max(
                            28, len(str(adjusted)) + len(rate.as_tuple().digits)
                            + abs(rate.as_tuple().exponent) + 8,
                        )
                        tax = int((Decimal(adjusted) * rate).quantize(
                            Decimal("1"), rounding=ROUND_HALF_UP,
                        ))
                    gross = adjusted + tax
                result[pid] = replace(old, unit_price_cents=adjusted, unit_gross_cents=gross)
        return result

    def availability(
        self, product_ids: list[str], delivery_date: date,
        sales_units: dict[str, float], agent_id: str,
    ) -> dict[str, AvailabilityInfo]:
        ids = _ids(product_ids)
        _date(delivery_date, "delivery_date")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("agent_id is required")
        if not isinstance(sales_units, dict) or set(sales_units) != set(ids):
            raise ValueError("sales_units must match the requested product codes")
        for amount in sales_units.values():
            if _number(amount, "sales_units") <= 0:
                raise ValueError("sales_units must be positive")
        remaining = self._before("availability", ids)
        if not remaining:
            return {}
        upstream = self.upstream.availability(
            remaining, delivery_date, {pid: sales_units[pid] for pid in remaining}, agent_id,
        )
        result = {pid: copy.deepcopy(upstream[pid]) for pid in remaining if pid in upstream}
        eligible = []
        for pid, info in result.items():
            if info.status == "YES":
                eligible.append(pid)
            elif info.status == "PARTIAL" and info.deliverable_sales_units is not None:
                if _number(info.deliverable_sales_units, "deliverable_sales_units") > 0:
                    eligible.append(pid)
        partial = self.store.take("PARTIAL_STOCK", "availability", eligible)
        if partial is not None:
            ratio = Decimal(str(partial.params.get("ratio", .5)))
            for pid in _target_ids(partial, eligible):
                old = result[pid]
                if old.status not in {"YES", "PARTIAL"}:
                    continue
                amount = float(Decimal(str(sales_units[pid])) * ratio)
                if old.status == "PARTIAL" and old.deliverable_sales_units is not None:
                    previous = _number(old.deliverable_sales_units, "deliverable_sales_units")
                    if previous < 0:
                        raise ValueError("deliverable_sales_units must be non-negative")
                    amount = min(amount, previous)
                amount = min(amount, sales_units[pid])
                result[pid] = (
                    AvailabilityInfo("PARTIAL", amount) if amount > 0
                    else AvailabilityInfo("NO", None)
                )
        return result
