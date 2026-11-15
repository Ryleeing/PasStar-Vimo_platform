"""Ranking: badges, order, and options that cannot be delivered on the requested date."""
from app.orchestrator.ranking import Draft, rank
from app.schemas import CandidateLine, Quantity, Totals

WEIGHTS = {"price": 0.3, "fulfilment": 0.5, "carbon": 0.1, "local": 0.1}


def draft(pid: str, gross: int, reliability: str = "HIGH", stock: str = "YES") -> Draft:
    line = CandidateLine(product_id=pid, name=pid, sales_unit="ST", sales_units=1, qty_covered=Quantity(value=1, unit="L"),
                         conversion_source="UNIT_CONVERSIONS", available_on_date=stock)
    return Draft(lines=[line], totals=Totals(net_cents=gross, vat_cents=0, deposit_cents=0, gross_cents=gross),
                 reliability_level=reliability)


def test_only_the_recommended_option_has_a_badge():
    out = rank([draft("a", 100), draft("b", 90, "MEDIUM"), draft("c", 120), draft("d", 130, "LOW")], WEIGHTS)
    assert [d.badge for d in out] == ["RECOMMENDED", None, None, None]
    assert [d.rank for d in out] == [1, 2, 3, 4]
    assert [d.score for d in out] == sorted((d.score for d in out), reverse=True)   # the rest by score


def test_undeliverable_options_come_last_without_badges():
    # The cheapest and best-scoring option cannot be delivered on the date: it must not be recommended.
    out = rank([draft("gone", 50, stock="NO"), draft("a", 100), draft("b", 110), draft("c", 120)], WEIGHTS)
    assert [d.lines[0].product_id for d in out][-1] == "gone"
    assert out[-1].badge is None and out[-1].rank == 4
    assert out[0].badge == "RECOMMENDED" and out[0].lines[0].product_id != "gone"


def test_unknown_stock_is_not_treated_as_undeliverable():
    out = rank([draft("unknown", 50, stock="UNKNOWN"), draft("a", 100)], WEIGHTS)
    assert out[0].lines[0].product_id == "unknown" and out[0].badge == "RECOMMENDED"


def test_nothing_deliverable_means_no_badges():
    out = rank([draft("x", 50, stock="NO"), draft("y", 60, stock="NO")], WEIGHTS)
    assert [d.badge for d in out] == [None, None] and [d.rank for d in out] == [1, 2]


def test_limit():
    out = rank([draft(str(i), 100 + i) for i in range(12)], WEIGHTS, limit=8)
    assert len(out) == 8 and out[-1].rank == 8
