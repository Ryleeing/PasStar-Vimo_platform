"""Scoped Valio price/availability access with bounded retries and probe budgets.

The client is bound to one authenticated buyer/customer mapping. It requests
only a narrowed shortlist, never returns raw stock, and stores probe budgets
in a separate writable runtime database. Tests inject an HTTP MockTransport;
normal construction does not make a network request.
"""
from __future__ import annotations

import copy
import math
import os
import sqlite3
import threading
import time
from collections import deque
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from ..internal import AvailabilityInfo, PriceInfo


HELSINKI = ZoneInfo("Europe/Helsinki")
DEFAULT_RUNTIME_DATABASE = Path(__file__).resolve().parents[2] / "data" / "runtime.sqlite"
MAX_PRODUCTS = 8


class ValioClientError(RuntimeError):
    """A sanitised upstream or local scope/limit failure safe for API translation."""

    def __init__(
        self, code: str, message: str, *, status_code: int | None = None,
        retryable: bool = False, retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after = retry_after


def _finite_number(value: object, *, positive: bool = False) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and (value > 0 if positive else value >= 0)
    except OverflowError:
        return False


def _decimal(value: object) -> Decimal | None:
    if not _finite_number(value):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        if len(value) == 10:
            return date.fromisoformat(value)
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # The upstream contract explicitly describes server timestamps as Helsinki
    # time, including deployments that omit their UTC offset.
    return parsed.replace(tzinfo=HELSINKI) if parsed.tzinfo is None else parsed


class ValioClient:
    def __init__(
        self, *, base_url: str, buyer_id: str, customer_id: str,
        api_key: str | None = None, http_client: httpx.Client | None = None,
        probe_db_path: str | Path | None = None, timeout: float = 5,
        cache_ttl: float = 15, max_attempts: int = 3,
        backoff_base: float = 0.25, max_backoff: float = 2,
        max_probes_per_day: int = 3, rate_limit_per_minute: int = 30,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
        sales_unit_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        parsed_url = urlsplit(base_url)
        if (
            parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc
            or parsed_url.username or parsed_url.password or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError("base_url must be an HTTP(S) origin/path without credentials or query")
        if not isinstance(buyer_id, str) or not buyer_id.strip():
            raise ValueError("buyer_id must be provided")
        if not isinstance(customer_id, str) or not customer_id.strip():
            raise ValueError("customer_id must be provided")
        secret = api_key if api_key is not None else os.environ.get("VALIO_API_KEY")
        if not isinstance(secret, str) or not secret.strip():
            raise ValueError("VALIO_API_KEY must be provided through the environment or constructor")
        for name, value in (
            ("timeout", timeout), ("cache_ttl", cache_ttl),
            ("backoff_base", backoff_base), ("max_backoff", max_backoff),
        ):
            if not _finite_number(value) or (name == "timeout" and value == 0):
                raise ValueError(f"{name} must be finite and non-negative; timeout must be positive")
        for name, value in (
            ("max_attempts", max_attempts), ("max_probes_per_day", max_probes_per_day),
            ("rate_limit_per_minute", rate_limit_per_minute),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if max_attempts > 5:
            raise ValueError("max_attempts must not exceed five")

        self.base_url = base_url.rstrip("/")
        self.buyer_id = buyer_id
        self.customer_id = customer_id
        self._api_key = secret
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self.max_backoff = max_backoff
        self.max_probes_per_day = max_probes_per_day
        self.rate_limit_per_minute = rate_limit_per_minute
        self._sleep = sleep
        self._monotonic = monotonic
        self._now = now or (lambda: datetime.now(HELSINKI))
        self._sales_unit_resolver = sales_unit_resolver
        self._http = http_client or httpx.Client(follow_redirects=False)
        self._owns_http = http_client is None
        self._cache: dict[tuple, tuple[float, dict]] = {}
        self._request_times: deque[float] = deque()
        self._lock = threading.RLock()

        runtime_path = probe_db_path if probe_db_path is not None else DEFAULT_RUNTIME_DATABASE
        if str(runtime_path) != ":memory:":
            runtime_path = Path(runtime_path)
            runtime_path.parent.mkdir(parents=True, exist_ok=True)
        self._probes = sqlite3.connect(runtime_path, check_same_thread=False, timeout=5)
        self._probes.executescript("""
            CREATE TABLE IF NOT EXISTS valio_probe_budgets (
                buyer_id TEXT NOT NULL,
                customer_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                day TEXT NOT NULL,
                count INTEGER NOT NULL,
                PRIMARY KEY (buyer_id, customer_id, agent_id, product_id, day)
            );
            CREATE TABLE IF NOT EXISTS availability_probes (
                agent_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                quantity REAL NOT NULL,
                at TEXT NOT NULL
            );
        """)

    @classmethod
    def from_env(cls, **overrides) -> "ValioClient":
        """Construct from explicit non-secret mapping and the local API-key environment."""
        values = {
            "base_url": os.environ.get("VALIO_API_BASE_URL", ""),
            "buyer_id": os.environ.get("VALIO_BUYER_ID", ""),
            "customer_id": os.environ.get("VALIO_CUSTOMER_ID", ""),
        }
        values.update(overrides)
        return cls(**values)

    def close(self) -> None:
        with self._lock:
            self._probes.close()
            self._cache.clear()
            if self._owns_http:
                self._http.close()

    def clear_cache(self) -> None:
        """Force fresh upstream checks before creating or rechecking a quote."""
        with self._lock:
            self._cache.clear()

    def __enter__(self) -> "ValioClient":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    @staticmethod
    def _ids(product_ids: list[str]) -> list[str]:
        if not isinstance(product_ids, list) or len(product_ids) > MAX_PRODUCTS:
            raise ValioClientError("VALIDATION_ERROR", "Query at most eight shortlisted products")
        if any(not isinstance(pid, str) or not pid.strip() for pid in product_ids):
            raise ValioClientError("VALIDATION_ERROR", "Product IDs must be non-empty strings")
        if len(set(product_ids)) != len(product_ids):
            raise ValioClientError("VALIDATION_ERROR", "Product IDs must be unique")
        return list(product_ids)

    @staticmethod
    def _delivery_date(value: date) -> date:
        if not isinstance(value, date) or isinstance(value, datetime):
            raise ValioClientError("VALIDATION_ERROR", "Delivery date must be a date")
        return value

    def _local_now(self) -> datetime:
        current = self._now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=HELSINKI)
        return current.astimezone(HELSINKI)

    def _cache_get(self, key: tuple) -> dict | None:
        with self._lock:
            item = self._cache.get(key)
            if item is None:
                return None
            expires, result = item
            if self._monotonic() >= expires:
                self._cache.pop(key, None)
                return None
            return copy.deepcopy(result)

    def _cache_put(self, key: tuple, result: dict, *, ttl: float | None = None) -> None:
        lifetime = self.cache_ttl if ttl is None else min(ttl, self.cache_ttl)
        if lifetime <= 0:
            return
        with self._lock:
            # Keep short caches bounded even when request dates/quantities vary.
            if len(self._cache) >= 128:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (self._monotonic() + lifetime, copy.deepcopy(result))

    def _consume_rate(self) -> None:
        with self._lock:
            current = self._monotonic()
            while self._request_times and self._request_times[0] <= current - 60:
                self._request_times.popleft()
            if len(self._request_times) >= self.rate_limit_per_minute:
                retry_after = max(0, self._request_times[0] + 60 - current)
                raise ValioClientError(
                    "RATE_LIMITED", "Local customer query rate exceeded", status_code=429,
                    retryable=True, retry_after=retry_after,
                )
            self._request_times.append(current)

    def _consume_probes(
        self, product_ids: list[str], sales_units: dict[str, float], agent_id: str,
    ) -> None:
        current = self._local_now()
        day = current.date().isoformat()
        with self._lock:
            try:
                self._probes.execute("BEGIN IMMEDIATE")
                for product_id in product_ids:
                    identity = (self.buyer_id, self.customer_id, agent_id, product_id, day)
                    row = self._probes.execute(
                        """SELECT count FROM valio_probe_budgets WHERE buyer_id=? AND
                           customer_id=? AND agent_id=? AND product_id=? AND day=?""",
                        identity,
                    ).fetchone()
                    if row is not None and row[0] >= self.max_probes_per_day:
                        raise ValioClientError(
                            "PROBE_LIMIT", "Daily availability probe limit exceeded",
                            status_code=429,
                        )
                for product_id in product_ids:
                    identity = (self.buyer_id, self.customer_id, agent_id, product_id, day)
                    self._probes.execute(
                        """INSERT INTO valio_probe_budgets VALUES (?,?,?,?,?,1)
                           ON CONFLICT(buyer_id,customer_id,agent_id,product_id,day)
                           DO UPDATE SET count=count+1""", identity,
                    )
                    self._probes.execute(
                        "INSERT INTO availability_probes VALUES (?,?,?,?)",
                        (agent_id, product_id, sales_units[product_id], current.isoformat()),
                    )
                self._probes.commit()
            except BaseException:
                self._probes.rollback()
                raise

    def _retry_after(self, response: httpx.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if raw is None:
            return None
        try:
            seconds = float(raw)
            return seconds if math.isfinite(seconds) and seconds >= 0 else None
        except ValueError:
            try:
                value = parsedate_to_datetime(raw)
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                return max(0, (value - self._local_now()).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None

    def _post(self, endpoint: str, product_ids: list[str], requested_date: date) -> dict:
        payload = {
            "customer": self.customer_id,
            "requestedDate": requested_date.isoformat(),
            "products": product_ids,
        }
        for attempt in range(self.max_attempts):
            self._consume_rate()
            try:
                response = self._http.post(
                    self.base_url + endpoint, json=payload,
                    headers={"X-API-Key": self._api_key}, timeout=self.timeout,
                    follow_redirects=False,
                )
            except (httpx.TimeoutException, httpx.TransportError):
                error = ValioClientError(
                    "UPSTREAM_ERROR", "Valio transport request failed", retryable=True,
                )
            else:
                if 200 <= response.status_code < 300:
                    invalid_json = False
                    try:
                        body = response.json()
                    except (ValueError, UnicodeDecodeError):
                        invalid_json = True
                    # Raise after the decoder's except block so __context__
                    # cannot retain a JSONDecodeError containing the raw body.
                    if invalid_json:
                        raise ValioClientError("UPSTREAM_ERROR", "Valio returned invalid JSON") from None
                    if not isinstance(body, dict):
                        raise ValioClientError("UPSTREAM_ERROR", "Valio returned an invalid response")
                    return body
                status = response.status_code
                retryable = status == 429 or status >= 500
                error = ValioClientError(
                    "RATE_LIMITED" if status == 429 else "UPSTREAM_ERROR",
                    "Valio request was rejected" if not retryable else "Valio is temporarily unavailable",
                    status_code=status, retryable=retryable,
                    retry_after=self._retry_after(response) if status == 429 else None,
                )
            if not error.retryable or attempt + 1 >= self.max_attempts:
                raise error from None
            delay = min(self.max_backoff, self.backoff_base * (2 ** attempt))
            if error.retry_after is not None:
                # A long Retry-After is returned to the caller instead of
                # sleeping less than the server requests and retrying early.
                if error.retry_after > self.max_backoff:
                    raise error from None
                delay = max(delay, error.retry_after)
            self._sleep(delay)
        raise AssertionError("unreachable retry loop")

    @staticmethod
    def _product_rows(body: dict, product_ids: list[str]) -> dict[str, dict]:
        rows = body.get("products")
        if not isinstance(rows, list):
            raise ValioClientError("UPSTREAM_ERROR", "Valio response has no product list")
        result = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("productId"), str):
                raise ValioClientError("UPSTREAM_ERROR", "Valio returned an invalid product row")
            product_id = row["productId"]
            if product_id not in product_ids or product_id in result:
                raise ValioClientError("UPSTREAM_ERROR", "Valio returned inconsistent product IDs")
            result[product_id] = row
        return result

    def prices(
        self, product_ids: list[str], buyer_id: str, *, delivery_date: date | None = None,
    ) -> dict[str, PriceInfo]:
        if buyer_id != self.buyer_id:
            raise ValioClientError("OUT_OF_SCOPE", "Buyer does not match the configured customer scope", status_code=403)
        ids = self._ids(product_ids)
        if not ids:
            return {}
        requested = self._delivery_date(
            self._local_now().date() if delivery_date is None else delivery_date
        )
        key = ("price", self.buyer_id, self.customer_id, requested, tuple(ids))
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        body = self._post("/api/price", ids, requested)
        if body.get("currency") != "EUR" or body.get("customerId") != self.customer_id:
            raise ValioClientError("UPSTREAM_ERROR", "Valio returned a mismatched price scope or currency")
        if _date(body.get("pricingDate")) != requested:
            raise ValioClientError("UPSTREAM_ERROR", "Valio returned prices for a different date")
        result = {}
        for product_id, row in self._product_rows(body, ids).items():
            parsed = self._price(row, requested)
            if parsed is not None:
                result[product_id] = parsed
        # Missing/error price rows are never cached as a confirmed zero price.
        if len(result) == len(ids):
            self._cache_put(key, result)
        return result

    @staticmethod
    def _price(row: dict, requested: date) -> PriceInfo | None:
        if row.get("error") is not False or row.get("quantity") != 1 or isinstance(row.get("quantity"), bool):
            return None
        catalog = row.get("catalogPrice")
        tax = row.get("tax")
        if not isinstance(catalog, dict) or not isinstance(tax, dict):
            return None
        net = _decimal(catalog.get("value"))
        rate = _decimal(tax.get("taxRate"))
        gross = _decimal(catalog.get("valueWithTax"))
        tax_value = _decimal(tax.get("taxValue"))
        if (
            net is None or rate is None or gross is None or tax_value is None
            or rate > 100 or catalog.get("currency") != "EUR"
        ):
            return None
        valid_from = _date(catalog.get("validFrom"))
        valid_to = _date(catalog.get("validTo"))
        if valid_from is None or valid_to is None or not valid_from <= requested <= valid_to:
            return None
        try:
            cents = int((net * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            tax_cents = int((tax_value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            gross_cents = int((gross * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            expected_tax = int(
                (Decimal(cents) * rate / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )
        except (InvalidOperation, OverflowError, ValueError):
            return None
        if (
            max(cents, tax_cents, gross_cents) > 2**63 - 1
            or tax_cents != expected_tax or gross_cents != cents + expected_tax
        ):
            return None
        price_with_tax = row.get("priceWithTax")
        if price_with_tax is not None:
            if not isinstance(price_with_tax, dict) or price_with_tax.get("currency") != "EUR":
                return None
            quoted_gross = _decimal(price_with_tax.get("value"))
            quoted_from = _date(price_with_tax.get("validFrom"))
            quoted_to = _date(price_with_tax.get("validTo"))
            if (
                quoted_gross is None or quoted_from is None or quoted_to is None
                or not quoted_from <= requested <= quoted_to
            ):
                return None
            try:
                quoted_cents = int(
                    (quoted_gross * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                )
            except (InvalidOperation, OverflowError, ValueError):
                return None
            if quoted_cents != gross_cents:
                return None
        # Deposit materials are not priced by this product endpoint. The
        # catalogue service withholds a complete total if deposits are listed.
        return PriceInfo(cents, float(rate / 100), 0, unit_gross_cents=gross_cents)

    def availability(
        self, product_ids: list[str], delivery_date: date,
        sales_units: dict[str, float], agent_id: str,
    ) -> dict[str, AvailabilityInfo]:
        ids = self._ids(product_ids)
        requested = self._delivery_date(delivery_date)
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValioClientError("VALIDATION_ERROR", "agent_id must be provided")
        if not isinstance(sales_units, dict) or set(sales_units) != set(ids):
            raise ValioClientError("VALIDATION_ERROR", "Sales quantities must match the requested products")
        if any(not _finite_number(value, positive=True) for value in sales_units.values()):
            raise ValioClientError("VALIDATION_ERROR", "Sales quantities must be finite and positive")
        if not ids:
            return {}
        self._consume_probes(ids, sales_units, agent_id)
        quantities = tuple((pid, float(sales_units[pid])) for pid in ids)
        key = ("stock", self.buyer_id, self.customer_id, requested, quantities)
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        body = self._post("/api/availability", ids, requested)
        if _date(body.get("requestedDate")) != requested:
            raise ValioClientError("UPSTREAM_ERROR", "Valio returned availability for a different date")
        rows = self._product_rows(body, ids)
        server_now = _timestamp(body.get("timeNow"))
        if server_now is None:
            raise ValioClientError("UPSTREAM_ERROR", "Valio returned invalid availability timing")
        result = {
            pid: self._availability(rows.get(pid), requested, float(sales_units[pid]), server_now)
            for pid in ids
        }
        ttl = self.cache_ttl
        for row in rows.values():
            cutoff = _timestamp(row.get("closingTime"))
            if cutoff is not None:
                current = max(self._local_now(), server_now) if server_now else self._local_now()
                ttl = min(ttl, max(0, (cutoff - current).total_seconds()))
        self._cache_put(key, result, ttl=ttl)
        return result

    def _availability(
        self, row: dict | None, requested: date, quantity: float,
        server_now: datetime | None,
    ) -> AvailabilityInfo:
        unknown = AvailabilityInfo("UNKNOWN", None)
        if row is None:
            return unknown
        if row.get("salesBlock") is True:
            return AvailabilityInfo("NO", None)
        if row.get("salesBlock") is not False:
            return unknown
        lines = row.get("scheduleLines")
        if not isinstance(lines, list):
            return unknown
        if not lines:
            return AvailabilityInfo("NO", None)
        if len(lines) != 1 or not isinstance(lines[0], dict):
            return unknown
        line = lines[0]
        if line.get("salesBlock") is True:
            return AvailabilityInfo("NO", None)
        if line.get("salesBlock") is not False:
            return unknown
        available_date = _date(line.get("availableDate"))
        if available_date is None:
            return unknown
        if available_date > requested:
            return AvailabilityInfo("NO", None)
        amount = line.get("availableAmount")
        if not _finite_number(amount):
            return unknown
        if amount == 0:
            return AvailabilityInfo("NO", None)
        cutoff = _timestamp(row.get("closingTime"))
        if row.get("closingTime") is not None and cutoff is None:
            return unknown
        if cutoff is not None:
            # Check both the local clock and server clock; a stale response must
            # not revive an order cutoff that has already passed locally.
            current = max(self._local_now(), server_now) if server_now else self._local_now()
            if current > cutoff:
                return AvailabilityInfo("NO", None)
        logistic_model = row.get("logisticModel")
        if not isinstance(logistic_model, str):
            return unknown
        if "terminal" in logistic_model.casefold() or amount == 999999:
            return unknown
        if logistic_model.casefold() not in {"stock", "stocked"}:
            return unknown
        if not isinstance(line.get("salesUnit"), str) or not line["salesUnit"]:
            return unknown
        if self._sales_unit_resolver is not None:
            expected_unit = self._sales_unit_resolver(row["productId"])
            if not expected_unit or line["salesUnit"] != expected_unit:
                return unknown
        if amount >= quantity:
            return AvailabilityInfo("YES", None)
        return AvailabilityInfo("PARTIAL", min(float(amount), quantity))
