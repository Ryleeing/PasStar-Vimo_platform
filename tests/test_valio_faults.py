from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from app.internal import AvailabilityInfo, PriceInfo
from app.schemas import FaultUpdate
from app.valio.client import ValioClientError
from app.valio.faults import FAULT_NAMES, FaultProxy, FaultStore


DAY = date(2026, 11, 16)


class Upstream:
    buyer_id = "buyer"

    def __init__(self):
        self.price_data = {"a": PriceInfo(105, .135, 2), "b": PriceInfo(200, .135, 0)}
        self.stock_data = {"a": AvailabilityInfo("YES", None), "b": AvailabilityInfo("PARTIAL", 3)}
        self.calls = []

    def prices(self, ids, buyer, *, delivery_date=None):
        self.calls.append(("prices", list(ids), buyer, delivery_date))
        return self.price_data

    def availability(self, ids, delivery_date, amounts, agent):
        self.calls.append(("availability", list(ids), delivery_date, dict(amounts), agent))
        return self.stock_data


def stock(proxy, ids=None, amounts=None):
    return proxy.availability(ids or ["a", "b"], DAY, amounts or {"a": 10, "b": 10}, "agent")


def test_disabled_proxy_returns_copies_and_forwards_context():
    upstream = Upstream()
    proxy = FaultProxy(upstream)
    assert all(not fault.enabled for fault in proxy.store.list())
    prices = proxy.prices(["a"], "buyer", delivery_date=DAY)
    assert prices == {"a": PriceInfo(105, .135, 2)}
    assert upstream.calls == [("prices", ["a"], "buyer", DAY)]
    prices["a"].unit_price_cents = 0
    assert upstream.price_data["a"].unit_price_cents == 105
    availability = stock(proxy)
    availability["b"].deliverable_sales_units = 0
    assert upstream.stock_data["b"].deliverable_sales_units == 3


def test_price_change_target_rounding_and_disable_restore_upstream():
    upstream = Upstream()
    proxy = FaultProxy(upstream)
    source = {"product_ids": ["a"], "multiplier": 1.1}
    proxy.store.set("PRICE_CHANGE", FaultUpdate(enabled=True, params=source))
    source["product_ids"].append("b")
    prices = proxy.prices(["a", "b"], "buyer")
    assert prices["a"] == PriceInfo(116, .135, 2)
    assert prices["b"].unit_price_cents == 200
    assert upstream.price_data["a"].unit_price_cents == 105
    proxy.store.set("PRICE_CHANGE", enabled=False)
    assert proxy.prices(["a"], "buyer")["a"].unit_price_cents == 105


def test_price_delta_and_negative_price_rejection():
    proxy = FaultProxy(Upstream())
    proxy.store.set("PRICE_CHANGE", enabled=True, params={"delta_cents": -5})
    assert proxy.prices(["a"], "buyer")["a"].unit_price_cents == 100
    proxy.store.set("PRICE_CHANGE", enabled=True, params={"delta_cents": -106})
    with pytest.raises(ValioClientError, match="negative price"):
        proxy.prices(["a"], "buyer")


@pytest.mark.parametrize(("params", "net", "gross"), [
    ({"multiplier": 2}, 210, 238),
    ({"delta_cents": -5}, 100, 114),
])
def test_price_change_updates_gross_consistently_without_mutating_upstream(params, net, gross):
    upstream = Upstream()
    upstream.price_data["a"] = PriceInfo(105, .135, 2, unit_gross_cents=119)
    proxy = FaultProxy(upstream)
    proxy.store.set("PRICE_CHANGE", enabled=True, params=params)
    result = proxy.prices(["a"], "buyer")
    assert result["a"] == PriceInfo(net, .135, 2, unit_gross_cents=gross)
    assert upstream.price_data["a"] == PriceInfo(105, .135, 2, unit_gross_cents=119)


def test_partial_stock_never_increases_known_partial_or_exposes_raw_stock():
    upstream = Upstream()
    proxy = FaultProxy(upstream)
    proxy.store.set("PARTIAL_STOCK", enabled=True)
    result = stock(proxy)
    assert result == {"a": AvailabilityInfo("PARTIAL", 5), "b": AvailabilityInfo("PARTIAL", 3)}
    assert upstream.stock_data["a"] == AvailabilityInfo("YES", None)
    assert upstream.stock_data["b"] == AvailabilityInfo("PARTIAL", 3)
    proxy.store.set("PARTIAL_STOCK", enabled=True, params={"ratio": 0})
    assert stock(proxy)["a"] == AvailabilityInfo("NO", None)


@pytest.mark.parametrize("status", ["NO", "UNKNOWN"])
def test_partial_fault_does_not_upgrade_unknown_or_unavailable_stock(status):
    upstream = Upstream()
    upstream.stock_data["a"] = AvailabilityInfo(status, None)
    proxy = FaultProxy(upstream)
    proxy.store.set("PARTIAL_STOCK", enabled=True)
    assert stock(proxy)["a"] == AvailabilityInfo(status, None)


@pytest.mark.parametrize("info", [
    AvailabilityInfo("NO", None), AvailabilityInfo("UNKNOWN", None),
    AvailabilityInfo("PARTIAL", None), AvailabilityInfo("PARTIAL", 0),
])
def test_partial_budget_is_not_consumed_without_available_target(info):
    upstream = Upstream()
    upstream.stock_data = {"a": info}
    proxy = FaultProxy(upstream)
    proxy.store.set("PARTIAL_STOCK", enabled=True, params={"times": 1})
    assert proxy.availability(["a"], DAY, {"a": 10}, "agent") == {"a": info}
    assert proxy.store.get("PARTIAL_STOCK").params["times"] == 1


def test_short_circuit_precedence_and_trigger_budgets():
    upstream = Upstream()
    proxy = FaultProxy(upstream)
    for name in ("TIMEOUT", "RATE_LIMIT", "EMPTY_RESULT"):
        proxy.store.set(name, enabled=True, params={"times": 1})
    with pytest.raises(ValioClientError) as timeout:
        proxy.prices(["a"], "buyer")
    assert timeout.value.code == "UPSTREAM_ERROR"
    assert proxy.store.get("TIMEOUT").enabled is False
    assert proxy.store.get("RATE_LIMIT").params["times"] == 1
    with pytest.raises(ValioClientError) as limited:
        proxy.prices(["a"], "buyer")
    assert limited.value.code == "RATE_LIMITED"
    assert limited.value.retry_after == 1
    assert proxy.prices(["a"], "buyer") == {}
    assert upstream.calls == []
    assert proxy.prices(["a"], "buyer")["a"].unit_price_cents == 105


def test_empty_result_removes_targets_before_network_and_other_faults_apply_to_remainder():
    upstream = Upstream()
    proxy = FaultProxy(upstream)
    proxy.store.set("EMPTY_RESULT", enabled=True, params={"product_ids": ["a"]})
    proxy.store.set("PRICE_CHANGE", enabled=True, params={"delta_cents": 10})
    assert proxy.prices(["a", "b"], "buyer") == {"b": PriceInfo(210, .135, 0)}
    assert upstream.calls[-1][1] == ["b"]
    availability = stock(proxy)
    assert availability == {"b": AvailabilityInfo("PARTIAL", 3)}
    assert upstream.calls[-1][3] == {"b": 10}


def test_endpoint_and_target_mismatches_do_not_consume_finite_budget():
    proxy = FaultProxy(Upstream())
    proxy.store.set("TIMEOUT", enabled=True, params={
        "endpoint": "availability", "product_ids": ["b"], "times": 1,
    })
    proxy.prices(["a", "b"], "buyer")
    proxy.availability(["a"], DAY, {"a": 10}, "agent")
    assert proxy.store.get("TIMEOUT").params["times"] == 1
    with pytest.raises(ValioClientError):
        stock(proxy)
    assert proxy.store.get("TIMEOUT").params["times"] == 0


def test_transform_budget_not_consumed_on_empty_upstream_response():
    upstream = Upstream()
    upstream.price_data = {}
    proxy = FaultProxy(upstream)
    proxy.store.set("PRICE_CHANGE", enabled=True, params={"times": 1})
    assert proxy.prices(["a"], "buyer") == {}
    assert proxy.store.get("PRICE_CHANGE").params["times"] == 1


@pytest.mark.parametrize(("name", "params"), [
    ("PRICE_CHANGE", {"multiplier": 0}),
    ("PRICE_CHANGE", {"multiplier": float("nan")}),
    ("PRICE_CHANGE", {"multiplier": 1.1, "delta_cents": 1}),
    ("PRICE_CHANGE", {"delta_cents": 1.5}),
    ("PARTIAL_STOCK", {"ratio": 1}),
    ("PARTIAL_STOCK", {"ratio": -1}),
    ("TIMEOUT", {"times": 0}),
    ("TIMEOUT", {"times": True}),
    ("TIMEOUT", {"product_ids": ["a", "a"]}),
    ("TIMEOUT", {"endpoint": "orders"}),
    ("TIMEOUT", {"unknown": "ignored"}),
    ("RATE_LIMIT", {"retry_after": float("inf")}),
])
def test_bad_configuration_is_rejected_atomically(name, params):
    store = FaultStore()
    store.set(name, enabled=True, params={"times": 2})
    before = store.get(name)
    with pytest.raises(ValueError):
        store.set(name, enabled=True, params=params)
    assert store.get(name) == before


def test_store_returns_independent_models_and_reset_disables_every_fault():
    store = FaultStore()
    result = store.set("PRICE_CHANGE", enabled=True, params={"product_ids": ["a"]})
    result.params["product_ids"].append("b")
    listed = store.list()
    listed[0].params["product_ids"].append("c")
    assert store.get("PRICE_CHANGE").params["product_ids"] == ["a"]
    assert {fault.name for fault in store.reset()} == set(FAULT_NAMES)
    assert all(not fault.enabled and fault.params == {} for fault in store.list())


def test_sqlite_store_persists_and_consumes_across_instances(tmp_path):
    path = tmp_path / "runtime.sqlite"
    with FaultStore(path) as first, FaultStore(path) as second:
        first.set("EMPTY_RESULT", enabled=True, params={"times": 1})
        assert second.get("EMPTY_RESULT").enabled
        assert second.take("EMPTY_RESULT", "prices", ["a"]) is not None
        assert first.take("EMPTY_RESULT", "prices", ["a"]) is None
        assert first.get("EMPTY_RESULT").params["times"] == 0
    with pytest.raises(RuntimeError, match="closed"):
        first.get("EMPTY_RESULT")


def test_concurrent_triggers_cannot_exceed_budget():
    store = FaultStore()
    store.set("TIMEOUT", enabled=True, params={"times": 3})
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(lambda _: store.take("TIMEOUT", "prices", ["a"]), range(12)))
    assert sum(result is not None for result in results) == 3


def test_request_validation_precedes_faults_and_prevents_upstream_queries():
    upstream = Upstream()
    proxy = FaultProxy(upstream)
    proxy.store.set("EMPTY_RESULT", enabled=True)
    with pytest.raises(ValueError, match="eight"):
        proxy.prices([str(index) for index in range(9)], "buyer")
    with pytest.raises(ValioClientError) as scope:
        proxy.prices(["a"], "another-buyer")
    assert scope.value.code == "OUT_OF_SCOPE"
    with pytest.raises(ValueError, match="positive"):
        proxy.availability(["a"], DAY, {"a": 0}, "agent")
    with pytest.raises(ValueError, match="finite"):
        proxy.availability(["a"], DAY, {"a": float("inf")}, "agent")
    with pytest.raises(ValueError, match="match"):
        proxy.availability(["a"], DAY, {"b": 10}, "agent")
    assert upstream.calls == []


def test_empty_id_list_returns_without_consuming_fault_budget():
    upstream = Upstream()
    proxy = FaultProxy(upstream)
    proxy.store.set("TIMEOUT", enabled=True, params={"times": 1})
    assert proxy.prices([], "buyer") == {}
    assert proxy.availability([], DAY, {}, "agent") == {}
    assert proxy.store.get("TIMEOUT").params["times"] == 1
    assert upstream.calls == []


def test_clear_cache_forwards_without_resetting_fault_configuration():
    class CachedUpstream(Upstream):
        def clear_cache(self):
            self.cleared = True

    upstream = CachedUpstream()
    proxy = FaultProxy(upstream)
    proxy.store.set("PRICE_CHANGE", enabled=True)
    proxy.clear_cache()
    assert upstream.cleared
    assert proxy.store.get("PRICE_CHANGE").enabled
