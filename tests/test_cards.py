from datetime import date, datetime, timezone

import pytest

from app.catalog.cards import CardsBuilder
from app.internal import EffectiveRules, ProductRecord
from app.schemas import (
    CandidateLine,
    GateDecision,
    ProtocolBody,
    Quantity,
    Requirement,
    StructuredRequest,
)


TODAY = date(2026, 11, 14)
UPDATED = datetime(2026, 1, 15, tzinfo=timezone.utc)
LACTOSE = Requirement(kind="CLAIM", code="FREE_FROM_LACTOSE")
NUT_FREE = Requirement(kind="ALLERGEN_FREE", code="AN")


def product(**changes):
    values = dict(
        id="milk", name_en="Test milk", brand="Test", vendor="Vendor",
        category="10400", sales_unit="KI", lot_size=1,
        qty_per_sales_unit=20, qty_unit="L", qty_source="UNIT_CONVERSIONS",
        claims={"FREE_FROM_LACTOSE": "STRUCTURED"},
        allergens={"AM": ("CONTAINS", "A")}, labels=[], flags=[],
        substitutions=[], deposits=[], delivery_reliability=0.98765,
        co2_kg_per_kg=1.25, has_synkka=True,
        synkka_updated_at=UPDATED, country="fi", has_claim_data=True,
        allergen_list_present=True,
    )
    values.update(changes)
    return ProductRecord(**values)


class Catalog:
    def __init__(self, record=None):
        self.record = record or product()

    def get(self, product_id):
        assert product_id == self.record.id
        return self.record


def rules(requirements=None):
    protocol = ProtocolBody(
        buyer_id="buyer", agent_id="agent", auto_approve_limit_cents=100_000,
        max_order_cents=200_000, allowed_categories=["10400"],
        hard_requirements=[], approver="manager", valid_until=date(2027, 1, 1),
    )
    return EffectiveRules(
        protocol=protocol, hard_requirements=requirements or [], excluded_brands=[],
        excluded_vendors=[], excluded_product_ids=[], auto_limit_cents=100_000,
        max_cents=200_000, weights={}, soft={},
    )


def request(requirements=None):
    return StructuredRequest(
        category="10400", quantity=Quantity(value=1000, unit="L"),
        delivery_date=date(2026, 11, 16), requirements=requirements or [],
    )


def line(**changes):
    values = dict(
        product_id="milk", name="Test milk", sales_unit="KI", sales_units=50,
        qty_covered=Quantity(value=1000, unit="L"),
        conversion_source="UNIT_CONVERSIONS", unit_price_cents=100,
        line_net_cents=5000, vat_rate=0.135, available_on_date="YES",
    )
    values.update(changes)
    return CandidateLine(**values)


def gate(outcome="AUTO"):
    return GateDecision(outcome=outcome, rules_hit=[], summary="Provided gate preview")


def builder(record=None, **kwargs):
    return CardsBuilder(Catalog(record), today=TODAY, **kwargs)


def test_lactose_does_not_imply_milk_free_and_unknown_nuts_remain_undeclared():
    milk_free = Requirement(kind="CLAIM", code="FREE_FROM_MILK")
    card = builder().allergen_card([line()], rules([LACTOSE, milk_free, NUT_FREE]))

    assert [(row.requirement.code, row.status) for row in card.rows] == [
        ("FREE_FROM_LACTOSE", "CLAIMED"),
        ("FREE_FROM_MILK", "CONTAINS"),
        ("AN", "UNDECLARED"),
    ]
    assert card.data_updated_at == UPDATED
    assert any("Lactose-free does not mean milk-free" in note for note in card.notes)
    assert any("does not establish allergen safety" in note for note in card.notes)


def test_build_includes_request_requirements_and_deduplicates_protocol_requirements():
    effective = rules([LACTOSE])
    effective.protocol.hard_requirements = [LACTOSE]
    cards = builder().build([line()], request([LACTOSE, NUT_FREE]), effective, gate())

    assert [row.requirement.code for row in cards.allergen.rows] == [
        "FREE_FROM_LACTOSE", "AN",
    ]
    assert cards.alternatives == []
    assert cards.trust.overall != "HIGH"


def test_high_trust_requires_explicit_requirements_prices_stock_and_reliability_thresholds():
    configured = builder(reliability_high=0.98, reliability_medium=0.9)
    score = configured.trust_score([line()], request(), rules([LACTOSE]), gate())

    assert score.model_dump() == dict(
        evidence="HIGH", match="HIGH", fulfilment="HIGH", compliance="HIGH",
        overall="HIGH", how_to_improve=None,
    )


def test_percentage_vat_cannot_be_treated_as_a_normalised_price():
    configured = builder(reliability_high=0.98, reliability_medium=0.9)
    score = configured.trust_score([line(vat_rate=13.5)], request(), rules([LACTOSE]), gate())
    assert score.fulfilment == "LOW"


def test_default_reliability_is_unknown_and_raw_value_never_appears_in_cards():
    cards = builder().build([line()], request(), rules([LACTOSE]), gate())

    assert builder().reliability_level(product()) == "UNKNOWN"
    assert cards.trust.fulfilment == "MEDIUM"
    assert "0.98765" not in cards.model_dump_json()
    assert any("UNKNOWN" in tip.text for tip in cards.tips if tip.kind == "RELIABILITY")


@pytest.mark.parametrize("availability", ["NO", "UNKNOWN"])
def test_missing_or_negative_deliverability_prevents_high_fulfilment(availability):
    score = builder().trust_score(
        [line(available_on_date=availability)], request(), rules(), gate()
    )
    assert score.fulfilment == "LOW"
    assert score.overall == "LOW"


def test_inferred_quantity_and_missing_price_reduce_trust():
    score = builder().trust_score(
        [line(conversion_source="NAME_PARSE", unit_price_cents=None, line_net_cents=None)],
        request(), rules([LACTOSE]), gate(),
    )
    assert score.evidence == "LOW"
    assert score.match == "MEDIUM"
    assert score.fulfilment == "LOW"
    assert score.overall == "LOW"


def test_supplied_gate_and_profile_exclusions_are_reflected_in_compliance():
    approved = builder().trust_score([line()], request(), rules(), gate("BUYER_APPROVAL"))
    assert approved.compliance == "MEDIUM"

    excluded = rules()
    excluded.excluded_brands = ["test"]
    score = builder().trust_score([line()], request(), excluded, gate())
    assert score.compliance == "LOW"
    assert score.overall == "LOW"


def test_factual_tips_do_not_invent_total_carbon_or_alternative_ids():
    record = product(
        min_temp_c=0, max_temp_c=6, case_volume_l=41.3,
        labels=["EU_ORGANIC_FARMING"], deposits=[{"material": "deposit"}],
    )
    cards = builder(record).build(
        [line(overbuy=Quantity(value=5, unit="L"))], request(), rules(), gate()
    )
    tips = {tip.kind: tip.text for tip in cards.tips}

    assert "extra quantity 5 L" in tips["ROUNDING"]
    assert "confirm its charge" in tips["DEPOSIT"]
    assert "between 0 and 6" in tips["STORAGE"]
    assert "41.3 L per case" in tips["COLD_STORAGE_SPACE"]
    assert "1.25 kg CO2-eq per kg" in tips["CARBON"]
    assert "not the order's total" in tips["CARBON"]
    assert cards.alternatives == []


def test_empty_lines_have_low_trust():
    score = builder().trust_score([], request(), rules(), gate())
    assert score.overall == "LOW"
    assert score.compliance == "LOW"


def test_temperature_metadata_is_not_shown_as_a_label_tip():
    record = product(labels=["coolTemperature", "warmTemperature", "freezeTemperature"])
    cards = builder(record).build([line()], request(), rules(), gate())
    assert not any(tip.kind == "LABEL" for tip in cards.tips)


def test_future_manufacturer_update_has_low_evidence():
    record = product(synkka_updated_at=datetime(2027, 1, 1, tzinfo=timezone.utc))
    score = builder(record).trust_score([line()], request(), rules(), gate())
    assert score.evidence == "LOW"


@pytest.mark.parametrize(
    "thresholds",
    [dict(reliability_high=0.9), dict(reliability_high=0.8, reliability_medium=0.9),
     dict(reliability_high=float("nan"), reliability_medium=0.9)],
)
def test_invalid_reliability_configuration_is_rejected(thresholds):
    with pytest.raises(ValueError):
        builder(**thresholds)
