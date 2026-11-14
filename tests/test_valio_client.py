from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from app.internal import AvailabilityInfo
from app.valio.client import ValioClient, ValioClientError


NOW = datetime(2026, 11, 14, 10, tzinfo=timezone.utc)
DAY = date(2026, 11, 16)


def price_row(product_id="p1", **changes):
    result = {
        "productId": product_id, "quantity": 1.0, "error": False,
        "netPriceFound": True,
        "catalogPrice": {
            "value": 17.60, "valueWithTax": 19.98, "currency": "EUR",
            "validFrom": "2026-11-01T00:00:00+0200",
            "validTo": "2026-11-30T23:59:59+0200",
        },
        "tax": {"taxRate": 13.5, "taxValue": 2.38},
    }
    result.update(changes)
    return result


def prices_body(rows=None, **changes):
    result = {
        "customerId": "customer", "currency": "EUR", "pricingDate": DAY.isoformat(),
        "products": [price_row()] if rows is None else rows,
    }
    result.update(changes)
    return result


def stock_row(product_id="p1", **changes):
    result = {
        "productId": product_id, "salesBlock": False, "logisticModel": "Stock",
        "closingTime": "2026-11-15T20:00:00+0200",
        "scheduleLines": [{
            "availableAmount": 80, "availableDate": DAY.isoformat(),
            "salesUnit": "KI", "salesBlock": False,
        }],
    }
    result.update(changes)
    return result


def stock_body(rows=None, **changes):
    result = {
        "timeNow": NOW.isoformat(), "requestedDate": DAY.isoformat() + "T00:00:00+0200",
        "products": [stock_row()] if rows is None else rows,
    }
    result.update(changes)
    return result


def client(handler, **changes):
    values = dict(
        base_url="https://valio.invalid", buyer_id="buyer", customer_id="customer",
        api_key="test-only-key", probe_db_path=":memory:", now=lambda: NOW,
        sleep=lambda _: None,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    values.update(changes)
    return ValioClient(**values)


def test_prices_use_delivery_date_customer_scope_and_decimal_cents():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=prices_body())

    with client(handler) as api:
        result = api.prices(["p1"], "buyer", delivery_date=DAY)
        assert result["p1"].unit_price_cents == 1760
        assert result["p1"].vat_rate == .135
        assert result["p1"].deposit_cents_per_unit == 0
        assert result["p1"].unit_gross_cents == 1998
        assert json.loads(requests[0].content) == {
            "customer": "customer", "requestedDate": DAY.isoformat(), "products": ["p1"]
        }
        assert requests[0].headers["X-API-Key"] == "test-only-key"
        result["p1"].unit_price_cents = 1
        assert api.prices(["p1"], "buyer", delivery_date=DAY)["p1"].unit_price_cents == 1760
        assert len(requests) == 1


def test_wrong_buyer_is_rejected_before_network():
    with client(lambda _: pytest.fail("unexpected network call")) as api:
        with pytest.raises(ValioClientError) as caught:
            api.prices(["p1"], "other", delivery_date=DAY)
        assert caught.value.code == "OUT_OF_SCOPE"


def test_explicit_invalid_price_date_is_not_replaced_with_today():
    with client(lambda _: pytest.fail("unexpected network call")) as api:
        with pytest.raises(ValioClientError) as caught:
            api.prices(["p1"], "buyer", delivery_date="")
        assert caught.value.code == "VALIDATION_ERROR"


def test_clearing_price_cache_forces_a_fresh_quote_check():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=prices_body())

    with client(handler) as api:
        api.prices(["p1"], "buyer", delivery_date=DAY)
        api.clear_cache()
        api.prices(["p1"], "buyer", delivery_date=DAY)
        assert len(calls) == 2


@pytest.mark.parametrize("changes", [
    {"error": True}, {"catalogPrice": None}, {"tax": None}, {"quantity": 2},
    {"quantity": True}, {"tax": {"taxRate": -1}}, {"tax": {"taxRate": 101}},
])
def test_unavailable_or_invalid_product_prices_do_not_become_zero(changes):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=prices_body([price_row(**changes)]))

    with client(handler) as api:
        assert api.prices(["p1"], "buyer", delivery_date=DAY) == {}
        assert api.prices(["p1"], "buyer", delivery_date=DAY) == {}
        assert len(calls) == 2


@pytest.mark.parametrize("case", [
    "fractional_net", "wrong_gross", "wrong_tax", "missing_tax_value",
    "quoted_gross", "quoted_currency", "quoted_expiry",
])
def test_inconsistent_price_facts_are_unconfirmed_and_never_cached(case):
    row = price_row()
    if case == "fractional_net":
        row["catalogPrice"]["value"] = 17.605
    elif case == "wrong_gross":
        row["catalogPrice"]["valueWithTax"] = 25
    elif case == "wrong_tax":
        row["tax"]["taxValue"] = 1
    elif case == "missing_tax_value":
        row["tax"].pop("taxValue")
    else:
        row["priceWithTax"] = {
            "value": 19.98, "currency": "EUR",
            "validFrom": "2026-11-01", "validTo": "2026-11-30",
        }
        if case == "quoted_gross":
            row["priceWithTax"]["value"] = 19.99
        elif case == "quoted_currency":
            row["priceWithTax"]["currency"] = "USD"
        else:
            row["priceWithTax"]["validTo"] = "2026-11-15"
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=prices_body([row]))

    with client(handler) as api:
        assert api.prices(["p1"], "buyer", delivery_date=DAY) == {}
        assert api.prices(["p1"], "buyer", delivery_date=DAY) == {}
    assert len(calls) == 2


def test_confirmed_tax_inclusive_price_is_preserved():
    row = price_row(priceWithTax={
        "value": 19.98, "currency": "EUR",
        "validFrom": "2026-11-01", "validTo": "2026-11-30",
    })
    with client(lambda _: httpx.Response(200, json=prices_body([row]))) as api:
        assert api.prices(["p1"], "buyer", delivery_date=DAY)["p1"].unit_gross_cents == 1998


@pytest.mark.parametrize("changes", [
    {"currency": "USD"}, {"customerId": "other"}, {"pricingDate": "2026-11-17"},
    {"pricingDate": "2026-11-16-not-a-date"},
])
def test_mismatched_price_envelopes_are_rejected(changes):
    with client(lambda _: httpx.Response(200, json=prices_body(**changes))) as api:
        with pytest.raises(ValioClientError) as caught:
            api.prices(["p1"], "buyer", delivery_date=DAY)
        assert caught.value.code == "UPSTREAM_ERROR"


@pytest.mark.parametrize("status", [401, 403, 422, 302])
def test_permanent_errors_are_not_retried_and_never_echo_response_bodies(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="sensitive-upstream-body", headers={"Location": "https://other.invalid"})

    with client(handler) as api:
        with pytest.raises(ValioClientError) as caught:
            api.prices(["p1"], "buyer", delivery_date=DAY)
        error = caught.value
        assert error.status_code == status
        assert not error.retryable
        assert len(calls) == 1
        assert "sensitive" not in str(error)
        assert "test-only-key" not in str(error)
        assert error.__cause__ is None


def test_rate_limit_and_server_failures_retry_with_bounded_backoff():
    responses = [
        httpx.Response(429, headers={"Retry-After": ".5"}),
        httpx.Response(503), httpx.Response(200, json=prices_body()),
    ]
    delays = []
    with client(lambda _: responses.pop(0), sleep=delays.append) as api:
        assert api.prices(["p1"], "buyer", delivery_date=DAY)["p1"].vat_rate == .135
    assert delays == [.5, .5]


def test_long_retry_after_is_returned_without_early_retry():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "120"})

    with client(handler) as api:
        with pytest.raises(ValioClientError) as caught:
            api.prices(["p1"], "buyer", delivery_date=DAY)
        assert caught.value.retry_after == 120
        assert caught.value.code == "RATE_LIMITED"
        assert len(calls) == 1


def test_timeouts_retry_then_raise_sanitised_error():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("transport-message-with-sensitive-data", request=request)

    with client(handler) as api:
        with pytest.raises(ValioClientError) as caught:
            api.prices(["p1"], "buyer", delivery_date=DAY)
        assert len(calls) == 3
        assert caught.value.retryable
        assert "sensitive" not in str(caught.value)
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None


@pytest.mark.parametrize("body", [
    {"products": "invalid"}, prices_body([price_row(), price_row()]),
    prices_body([price_row("unrequested")]),
])
def test_bad_response_shapes_are_rejected(body):
    with client(lambda _: httpx.Response(200, json=body)) as api:
        with pytest.raises(ValioClientError):
            api.prices(["p1"], "buyer", delivery_date=DAY)


def test_invalid_json_is_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text="not-json")

    with client(handler) as api:
        with pytest.raises(ValioClientError) as caught:
            api.prices(["p1"], "buyer", delivery_date=DAY)
        assert len(calls) == 1
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None


@pytest.mark.parametrize("kind, expected, partial", [
    ("stock", "YES", None), ("partial", "PARTIAL", 20), ("zero", "NO", None),
    ("blocked", "NO", None), ("line_blocked", "NO", None), ("empty", "NO", None),
    ("late", "NO", None), ("terminal", "UNKNOWN", None), ("sentinel", "UNKNOWN", None),
    ("missing_date", "UNKNOWN", None), ("cutoff", "NO", None),
    ("invalid_date", "UNKNOWN", None), ("huge_amount", "UNKNOWN", None),
    ("unknown_model", "UNKNOWN", None),
])
def test_availability_semantics_expose_only_status_and_capped_partial(kind, expected, partial):
    row = stock_row()
    line = row["scheduleLines"][0]
    if kind == "partial":
        line["availableAmount"] = 20
    elif kind == "zero":
        line["availableAmount"] = 0
    elif kind == "blocked":
        row["salesBlock"] = True
    elif kind == "line_blocked":
        line["salesBlock"] = True
    elif kind == "empty":
        row["scheduleLines"] = []
    elif kind == "late":
        line["availableDate"] = "2026-11-17"
    elif kind == "terminal":
        row["logisticModel"] = "Terminal"
        line["availableAmount"] = 999999
    elif kind == "sentinel":
        line["availableAmount"] = 999999
    elif kind == "missing_date":
        line["availableDate"] = None
    elif kind == "invalid_date":
        line["availableDate"] = "2026-11-16-not-a-date"
    elif kind == "huge_amount":
        line["availableAmount"] = 10 ** 400
    elif kind == "cutoff":
        row["closingTime"] = "2026-11-14T11:00:00+0200"
    elif kind == "unknown_model":
        row["logisticModel"] = None
    with client(lambda _: httpx.Response(200, json=stock_body([row]))) as api:
        info = api.availability(["p1"], DAY, {"p1": 50}, "agent")["p1"]
        assert info == AvailabilityInfo(expected, partial)
        assert "999999" not in str(info)
        if info.deliverable_sales_units is not None:
            assert info.deliverable_sales_units <= 50


@pytest.mark.parametrize("timing", [None, "invalid", 0])
def test_unverified_server_time_cannot_confirm_stock_or_enter_cache(timing):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=stock_body(timeNow=timing))

    with client(handler) as api:
        for _ in range(2):
            with pytest.raises(ValioClientError) as caught:
                api.availability(["p1"], DAY, {"p1": 50}, "agent")
            assert caught.value.code == "UPSTREAM_ERROR"
    assert len(calls) == 2


def test_probe_budget_persists_across_clients_and_cache_hits(tmp_path):
    path = tmp_path / "runtime.sqlite"
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=stock_body())

    with client(handler, probe_db_path=path) as api:
        api.availability(["p1"], DAY, {"p1": 50}, "agent")
        api.availability(["p1"], DAY, {"p1": 50}, "agent")
        assert len(calls) == 1
    with client(handler, probe_db_path=path) as api:
        api.availability(["p1"], DAY, {"p1": 50}, "agent")
        with pytest.raises(ValioClientError) as caught:
            api.availability(["p1"], DAY, {"p1": 60}, "agent")
        assert caught.value.code == "PROBE_LIMIT"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count FROM valio_probe_budgets").fetchone()[0] == 3
        assert db.execute("SELECT count(*) FROM availability_probes").fetchone()[0] == 3
    assert len(calls) == 2


def test_catalogue_sales_unit_mismatch_keeps_availability_unknown():
    with client(lambda _: httpx.Response(200, json=stock_body()),
                sales_unit_resolver=lambda _: "ST") as api:
        assert api.availability(["p1"], DAY, {"p1": 50}, "agent") == {
            "p1": AvailabilityInfo("UNKNOWN", None)
        }


def test_batch_probe_limit_is_atomic(tmp_path):
    path = tmp_path / "runtime.sqlite"
    with client(lambda _: httpx.Response(200, json=stock_body()), probe_db_path=path,
                max_probes_per_day=1) as api:
        api.availability(["p1"], DAY, {"p1": 50}, "agent")
        with pytest.raises(ValioClientError):
            api.availability(["p2", "p1"], DAY, {"p1": 50, "p2": 1}, "agent")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT product_id,count FROM valio_probe_budgets").fetchall() == [("p1", 1)]


def test_retry_attempts_do_not_consume_extra_probe_budget(tmp_path):
    path = tmp_path / "runtime.sqlite"
    responses = [httpx.Response(503), httpx.Response(200, json=stock_body())]
    with client(lambda _: responses.pop(0), probe_db_path=path) as api:
        assert api.availability(["p1"], DAY, {"p1": 50}, "agent")["p1"].status == "YES"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count FROM valio_probe_budgets").fetchone()[0] == 1


def test_stock_cache_is_quantity_specific_and_does_not_outlive_cutoff():
    current = [NOW]
    clock = [0.0]
    calls = []
    row = stock_row(closingTime=(NOW + timedelta(seconds=5)).isoformat())

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=stock_body([row]))

    with client(handler, now=lambda: current[0], monotonic=lambda: clock[0],
                max_probes_per_day=5) as api:
        assert api.availability(["p1"], DAY, {"p1": 50}, "agent")["p1"].status == "YES"
        assert api.availability(["p1"], DAY, {"p1": 100}, "agent")["p1"].status == "PARTIAL"
        current[0] += timedelta(seconds=6)
        clock[0] += 6
        assert api.availability(["p1"], DAY, {"p1": 50}, "agent")["p1"].status == "NO"
    assert len(calls) == 3


def test_local_rate_limit_counts_real_attempts_and_can_expire():
    clock = [0.0]
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=prices_body())

    with client(handler, monotonic=lambda: clock[0], rate_limit_per_minute=1,
                cache_ttl=0) as api:
        api.prices(["p1"], "buyer", delivery_date=DAY)
        with pytest.raises(ValioClientError) as caught:
            api.prices(["p1"], "buyer", delivery_date=DAY)
        assert caught.value.code == "RATE_LIMITED"
        assert caught.value.retry_after == 60
        clock[0] = 61
        api.prices(["p1"], "buyer", delivery_date=DAY)
    assert len(calls) == 2


@pytest.mark.parametrize("ids, amounts", [
    (["p1"] * 2, {"p1": 1}), ([str(i) for i in range(9)], {}),
    (["p1"], {}), (["p1"], {"p1": float("inf")}),
    (["p1"], {"p1": -1}), (["p1"], {"p1": True}),
])
def test_invalid_shortlists_and_quantities_fail_before_network(ids, amounts):
    with client(lambda _: pytest.fail("unexpected network call")) as api:
        with pytest.raises(ValioClientError) as caught:
            api.availability(ids, DAY, amounts, "agent")
        assert caught.value.code == "VALIDATION_ERROR"


def test_empty_queries_do_not_call_the_network():
    with client(lambda _: pytest.fail("unexpected network call")) as api:
        assert api.prices([], "buyer", delivery_date=DAY) == {}
        assert api.availability([], DAY, {}, "agent") == {}


def test_environment_constructor_requires_explicit_scope_and_never_displays_key(monkeypatch):
    monkeypatch.setenv("VALIO_API_BASE_URL", "https://valio.invalid")
    monkeypatch.setenv("VALIO_API_KEY", "test-only-environment-key")
    monkeypatch.setenv("VALIO_BUYER_ID", "buyer")
    monkeypatch.setenv("VALIO_CUSTOMER_ID", "customer")
    with ValioClient.from_env(probe_db_path=":memory:") as api:
        assert api.buyer_id == "buyer"
        assert "test-only-environment-key" not in repr(api)
