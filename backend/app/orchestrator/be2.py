"""Which catalogue, units, cards and Valio implementations BE3's pipeline runs on.

  live  BE2's real implementations: the product catalogue built by backend/scripts/preprocess.py
        (backend/data/products.sqlite) and the Valio price / stock API (VALIO_API_* in the environment).
  stub  BE3's in-memory stand-ins: a handful of real product IDs with made-up prices. Used by the tests
        and on machines without the catalogue file or the API key.

THE_PASS_BE2 = auto (default: live when both the catalogue and VALIO_API_KEY are present) | live | stub.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional

from ..errors import ApiError
from ..internal import AvailabilityInfo, CardsApi, CatalogApi, PriceInfo, ProductRecord, UnitsApi, ValioApi
from ..schemas import Requirement

log = logging.getLogger(__name__)

# product, requirement -> True when the requirement is not proven for this product (gate rule R-DATA-UNVERIFIED)
Unverified = Callable[[ProductRecord, Requirement], bool]


@dataclass
class Be2:
    mode: str
    catalog: CatalogApi
    units: UnitsApi
    cards: CardsApi
    valio: ValioApi
    unverified: Unverified


class ScopedValio:
    """BE2's Valio client as the pipeline and BE1 need it.

    - The client is bound to one buyer (VALIO_BUYER_ID). Other buyers get no prices, so their options come
      back without a price instead of failing the whole request.
    - Client errors become the shared ApiError (RATE_LIMITED, PROBE_LIMIT, UPSTREAM_ERROR, ...)."""

    def __init__(self, upstream) -> None:
        self.upstream = upstream

    @property
    def buyer_id(self) -> Optional[str]:
        return getattr(self.upstream, "buyer_id", None)

    def prices(self, product_ids: list[str], buyer_id: str, *, delivery_date: Optional[date] = None) -> dict[str, PriceInfo]:
        if buyer_id != self.buyer_id:
            log.info("No Valio price scope for buyer %s (configured: %s)", buyer_id, self.buyer_id)
            return {}
        return self._call(lambda: self.upstream.prices(product_ids, buyer_id, delivery_date=delivery_date))

    def availability(self, product_ids: list[str], delivery_date: date, sales_units: dict[str, float],
                     agent_id: str) -> dict[str, AvailabilityInfo]:
        return self._call(lambda: self.upstream.availability(product_ids, delivery_date, sales_units, agent_id))

    @staticmethod
    def _call(fn):
        from ..valio.client import ValioClientError
        try:
            return fn()
        except ValioClientError as e:
            code = e.code if e.code in _KNOWN_CODES else "UPSTREAM_ERROR"
            raise ApiError(code, e.message, **({"retry_after": e.retry_after} if e.retry_after else {})) from e


_KNOWN_CODES = {"VALIDATION_ERROR", "OUT_OF_SCOPE", "RATE_LIMITED", "PROBE_LIMIT", "UPSTREAM_ERROR"}


def mode() -> str:
    wanted = os.environ.get("THE_PASS_BE2", "auto")
    if wanted not in ("auto", "live", "stub"):
        raise ValueError(f"THE_PASS_BE2 must be auto, live or stub, not {wanted!r}")
    if wanted != "auto":
        return wanted
    from ..catalog.repository import DEFAULT_DATABASE
    return "live" if DEFAULT_DATABASE.exists() and os.environ.get("VALIO_API_KEY") else "stub"


def _stub() -> Be2:
    from .stubs import PRODUCTS, StubCards, StubCatalog, StubUnits, StubValio

    def unverified(product: ProductRecord, r: Requirement) -> bool:
        return r.kind == "CLAIM" and PRODUCTS[product.id].claims.get(r.code) != "STRUCTURED"
    return Be2("stub", StubCatalog(), StubUnits(), StubCards(), StubValio(), unverified)


def _live() -> Be2:
    from ..catalog.cards import CardsBuilder
    from ..catalog.repository import CatalogRepository, requirement_check
    from ..catalog.units import UnitsConverter
    from ..valio.client import ValioClient
    from ..valio.faults import FaultProxy, FaultStore

    catalog = CatalogRepository()
    client = ValioClient.from_env(sales_unit_resolver=lambda product_id: catalog.get(product_id).sales_unit)

    def unverified(product: ProductRecord, r: Requirement) -> bool:
        return requirement_check(product, r, date.today()).verdict != "SATISFIED"
    return Be2("live", catalog, UnitsConverter(), CardsBuilder(catalog),
               ScopedValio(FaultProxy(client, FaultStore())), unverified)


_current: Optional[Be2] = None


def get() -> Be2:
    """The implementations for this process, built once."""
    global _current
    if _current is None:
        _current = _live() if mode() == "live" else _stub()
        log.info("BE3 pipeline runs on BE2 %s implementations", _current.mode)
    return _current
