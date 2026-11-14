from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest
import yaml
from pydantic.fields import FieldInfo

from app.catalog.disclosure import DisclosureFilter
from app.internal import Audience
from app.schemas import Candidate


NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


def audience(kind="AGENT", ceiling="L1"):
    return Audience(kind=kind, buyer_id="buyer", agent_id="agent", ceiling=ceiling)


@pytest.fixture
def policy(tmp_path):
    config = {
        "L0": [
            "product.id", "product.name", "product.brand", "product.claims",
            "product.labels", "product.allergens", "product.qty_per_sales_unit",
            "product.qty_unit", "product.has_manufacturer_data", "product.flags",
        ],
        "L1": [
            "price.unit_price_cents", "availability.status", "availability.deliverable_sales_units",
            "totals", "quote", "order", "profile", "candidates", "blocked", "request",
            "events", "reliability_level", "unconvertible_product_ids", "no_candidate_reason",
            "dropped_count",
        ],
        "L2": ["stock.quantity", "delivery_reliability.raw", "supplier.performance"],
        "L3": ["cost", "margin", "other_customer.*", "internal_system.*", "quote.jws"],
    }
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


@pytest.fixture
def disclosure(policy, tmp_path):
    return DisclosureFilter(policy, db_path=tmp_path / "runtime.sqlite", now=lambda: NOW)


def test_product_fields_are_default_deny_and_nested_rows_do_not_open_private_keys(disclosure):
    value = {
        "id": "milk", "name": "Milk", "cost": 123,
        "allergens": [{"code": "AM", "label": "Milk", "status": "CONTAINS", "trust_grade": "A", "margin": 999}],
        "claims": ["FREE_FROM_LACTOSE", {"supplier": {"performance": 999}}],
        "flags": ["KNOWN", {"internal_system": "private"}],
        "product.name": "spoofed",
    }
    assert disclosure.filter_product(value, audience(ceiling="L0")) == {
        "id": "milk", "name": "Milk",
        "allergens": [{"code": "AM", "label": "Milk", "status": "CONTAINS", "trust_grade": "A"}],
        "claims": ["FREE_FROM_LACTOSE"], "flags": ["KNOWN"],
    }


def test_parent_policy_entries_expand_known_leaves_without_allowing_arbitrary_children(disclosure):
    source = {
        "totals": {"gross_cents": 100, "margin": 50},
        "quote": {
            "id": "quote", "jws": "blocked-explicitly", "internal_system": {"stock": 900},
            "lines": [{"product_id": "milk", "unit_price_cents": 80, "cost": 60}],
            "totals": {"net_cents": 80, "cost": 999},
        },
        "profile": {
            "buyer_id": "buyer", "body": {
                "name": "Kitchen", "hard_exclusions": {"brands": ["Arla"], "other_customer": "private"},
                "internal_system": "private",
            },
        },
        "order": {"id": "order", "margin": 200, "gate": {"outcome": "AUTO", "cost": 50}},
    }
    result = disclosure.filter(source, audience())
    assert result["totals"] == {"gross_cents": 100}
    assert result["quote"] == {
        "id": "quote", "lines": [{"product_id": "milk", "unit_price_cents": 80}],
        "totals": {"net_cents": 80},
    }
    assert result["profile"]["body"] == {"name": "Kitchen", "hard_exclusions": {"brands": ["Arla"]}}
    assert result["order"] == {"id": "order", "gate": {"outcome": "AUTO"}}


def test_new_schema_fields_do_not_automatically_become_public(policy, tmp_path, monkeypatch):
    monkeypatch.setitem(Candidate.model_fields, "supplier_margin", FieldInfo(annotation=float))
    service = DisclosureFilter(policy, db_path=tmp_path / "audit.sqlite")
    assert service.filter({
        "candidates": [{"id": "candidate", "supplier_margin": 999.0}],
    }, audience()) == {"candidates": [{"id": "candidate"}]}


@pytest.mark.parametrize("kind", ["AGENT", "BUYER_USER", "LLM", "VALIO_USER"])
def test_audience_claiming_l3_does_not_open_private_levels(disclosure, kind):
    result = disclosure.filter({
        "product": {"id": "milk"}, "stock": {"quantity": 999},
        "delivery_reliability": {"raw": .987}, "supplier": {"performance": .98},
        "margin": 123, "other_customer": {"sales": 999},
    }, audience(kind=kind, ceiling="L3"))
    assert result == {"product": {"id": "milk"}}


def test_internal_l2_requires_explicit_opt_in_and_matching_ceiling(policy, tmp_path):
    service = DisclosureFilter(policy, db_path=tmp_path / "audit.sqlite", allow_internal_l2=True)
    payload = {"stock": {"quantity": 999}, "margin": 123}
    assert service.filter(payload, audience("VALIO_USER", "L1")) == {}
    assert service.filter(payload, audience("AGENT", "L3")) == {}
    assert service.filter(payload, audience("VALIO_USER", "L3")) == {"stock": {"quantity": 999}}


def test_llm_pack_forces_llm_scope_and_tracks_actual_released_level(policy, tmp_path):
    service = DisclosureFilter(policy, db_path=tmp_path / "audit.sqlite", allow_internal_l2=True)
    source = {"product": {"id": "milk"}, "stock": {"quantity": 999}}
    pack = service.pack_for_llm(source, audience("VALIO_USER", "L3"))
    assert pack.data == {"product": {"id": "milk"}}
    assert pack.max_sensitivity == "L0"
    pack = service.pack_for_llm({"totals": {"gross_cents": 100}}, audience())
    assert pack.max_sensitivity == "L1"
    assert service.pack_for_llm({"margin": 999}, audience()).max_sensitivity == "L0"


def test_l0_ceiling_removes_known_l1_fields(disclosure):
    assert disclosure.filter({
        "product": {"id": "milk"}, "price": {"unit_price_cents": 100},
        "totals": {"gross_cents": 100},
    }, audience(ceiling="L0")) == {"product": {"id": "milk"}}


def test_literal_dotted_and_wildcard_keys_cannot_impersonate_paths(disclosure):
    assert disclosure.filter({
        "product.id": "spoofed", "product.*": "private",
        "product": {"id": "milk", "name.internal_system": "private", "*": "private"},
    }, audience()) == {"product": {"id": "milk"}}


def test_filter_does_not_mutate_input_or_share_mutable_containers(disclosure):
    source = {"product": {"id": "milk", "claims": ["FREE_FROM_LACTOSE"]}}
    original = deepcopy(source)
    result = disclosure.filter(source, audience())
    result["product"]["claims"].append("VEGAN")
    assert source == original


def test_audit_records_metadata_without_retaining_values(disclosure):
    disclosure.filter({"product": {"id": "milk", "name": "do-not-log-value"}}, audience(), request_id="request")
    with sqlite3.connect(disclosure.db_path) as db:
        rows = db.execute(
            "SELECT at,agent_id,request_id,product_id,field,sensitivity FROM disclosures ORDER BY field"
        ).fetchall()
    assert rows == [
        (NOW.isoformat(), "agent", "request", "milk", "product.id", "L0"),
        (NOW.isoformat(), "agent", "request", "milk", "product.name", "L0"),
    ]
    assert "do-not-log-value" not in repr(rows)


def test_unknown_product_id_cannot_inject_audit_metadata(disclosure):
    disclosure.filter({"totals": {"gross_cents": 100, "product_id": "hidden-value"}}, audience())
    with sqlite3.connect(disclosure.db_path) as db:
        assert db.execute("SELECT product_id FROM disclosures").fetchall() == [(None,)]


def test_policy_denied_product_id_is_not_copied_into_audit_metadata(policy):
    config = yaml.safe_load(policy.read_text())
    config["L2"].append("product.id")
    policy.write_text(yaml.safe_dump(config), encoding="utf-8")
    db = sqlite3.connect(":memory:")
    try:
        disclosure = DisclosureFilter(policy, connection=db)
        result = disclosure.filter({"product": {"id": "hidden-id", "name": "Milk"}}, audience())
        assert result == {"product": {"name": "Milk"}}
        assert db.execute("SELECT product_id FROM disclosures").fetchall() == [(None,)]
    finally:
        db.close()


def test_failed_audit_prevents_payload_return(policy):
    db = sqlite3.connect(":memory:")
    db.close()
    service = DisclosureFilter(policy, connection=db)
    with pytest.raises(sqlite3.ProgrammingError):
        service.filter({"product": {"id": "milk"}}, audience())


@pytest.mark.parametrize("field", [
    ("product.margin", "L0", None),
    ("totals.gross_cents", "L0", None),
    ("stock.quantity", "L2", None),
    ("quote.jws", "L3", None),
])
def test_explicit_record_cannot_forge_out_of_scope_disclosures(disclosure, field):
    with pytest.raises(ValueError, match="scope"):
        disclosure.record(audience(), None, [field])


def test_wrong_shapes_and_nonfinite_numbers_are_not_serialized(disclosure):
    assert disclosure.filter({
        "product": {"id": {"margin": 999}, "name": ["not-a-string"]},
        "totals": {"gross_cents": True},
        "availability": {"status": "999999", "deliverable_sales_units": float("inf")},
    }, audience()) == {"product": {}, "totals": {}, "availability": {}}
