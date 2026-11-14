"""ScopedValio: the demo buyers share the competition's one Valio account; other buyers get no prices."""
from app.orchestrator.be2 import ScopedValio


class Upstream:
    buyer_id = "buy_hotel"

    def __init__(self):
        self.asked = []

    def prices(self, ids, buyer_id, *, delivery_date=None):
        self.asked.append(buyer_id)
        return {i: f"price-for-{buyer_id}" for i in ids}


def test_demo_buyers_price_as_the_configured_account():
    up = Upstream()
    v = ScopedValio(up)
    for buyer in ("buy_hotel", "buy_bistro", "buy_hospital"):
        assert v.prices(["p1"], buyer) == {"p1": "price-for-buy_hotel"}
    assert up.asked == ["buy_hotel"] * 3


def test_unknown_buyers_get_no_prices(monkeypatch):
    up = Upstream()
    assert ScopedValio(up).prices(["p1"], "buy_stranger") == {} and up.asked == []


def test_sharing_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("VALIO_SHARED_BUYERS", "")
    up = Upstream()
    v = ScopedValio(up)
    assert v.prices(["p1"], "buy_bistro") == {} and v.prices(["p1"], "buy_hotel") == {"p1": "price-for-buy_hotel"}


def test_no_prices_is_said_plainly_in_step_3():
    from app.internal import DisclosablePack
    from app.orchestrator.explain import TemplateExplain
    text = TemplateExplain().node_summary(3, DisclosablePack(data={"count": 0, "converted": 5, "priced": 0, "weights": "", "top": None}, max_sensitivity="L1"))
    assert "no prices" in text
