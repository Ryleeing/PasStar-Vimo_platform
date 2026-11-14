"""BE1 channel & transactions, continued: isolation between buyers, external authorization, approve with changes,
protocol change approvals, the rate limit window, the hold report, the audit chain and approval cards.
Uses the fixture and helpers of test_channel.py."""
from collections import defaultdict, deque
from datetime import datetime, timedelta

import pytest

from app import db, deps, fixtures as fx
from app.channel import audit, checkpoints, protocols, quotes
from app.errors import ApiError
from app.internal import Audience, AvailabilityInfo
from test_channel import (BULK, CHEF, EXT_AGENT, HOTEL_AGENT, MANAGER, OWNER, VALIO, c, decide, holds,  # noqa: F401
                          order, quote, token)

AWC = "APPROVE_WITH_CHANGES"
EXT = "prot_hotel_ext_v1"
MORE_CATEGORIES = {"allowed_categories": ["10400", "10401", "10405", "22540"]}      # one category more than prot_hotel_v1


def bistro_order(c, key="b1"):
    """Hand req_1 to the bistro and place an order as its owner."""
    db.run("UPDATE requests SET created_by = 'usr_owner', buyer_id = 'buy_bistro'")
    q = quote(fx.CAND_1, "prot_bistro_v1")
    return q, order(c, q.id, key, headers=OWNER, protocol_id="prot_bistro_v1").json()


def authorize(c, order_id, jwt, headers=EXT_AGENT):
    return c.post(f"/v1/orders/{order_id}/authorization", json={"buyer_authorization": jwt}, headers=headers)


def expansion(c):
    """An amend that widens prot_hotel_v1: prot_hotel_v2 waits for a signature, with a PROTOCOL_CHANGE approval."""
    return c.post("/v1/protocols/prot_hotel_v1/amend", json={"changes": MORE_CATEGORIES}, headers=HOTEL_AGENT).json()["approval_id"]


def order_count():
    return db.one("SELECT COUNT(*) AS n FROM orders")["n"]


# ───── P0: isolation between buyers, consistent state ─────

def test_other_buyer_cannot_read_a_quote_or_an_order(c):
    q = quote(fx.CAND_1)
    o = order(c, q.id, "k1").json()
    for url in (f"/v1/quotes/{q.id}", f"/v1/orders/{o['id']}"):
        assert c.get(url, headers=CHEF).status_code == 200
        r = c.get(url, headers=OWNER)
        assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_agent_cannot_read_another_buyers_quote_or_order(c):
    q, o = bistro_order(c)
    for url in (f"/v1/quotes/{q.id}", f"/v1/orders/{o['id']}"):
        assert c.get(url, headers=OWNER).status_code == 200
        r = c.get(url, headers=HOTEL_AGENT)
        assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_same_key_with_another_quote_returns_the_first_order(c):
    """Pins today's behaviour: the key alone decides and the request body is not compared.
    Stripe answers this with 400 idempotency_error; the IETF Idempotency-Key draft says 422."""
    first = order(c, quote(fx.CAND_1).id, "k1").json()
    other = quote(fx.CAND_1)
    assert first["status"] == "PLACED" and first["quote_id"] != other.id
    assert order(c, other.id, "k1").json() == first
    assert quotes.get(other.id).status == "VALID" and order_count() == 1          # the second quote was never looked at


def test_used_quote_is_refused_as_expired(c):
    q = quote(fx.CAND_1)
    quotes.set_status(q.id, "USED")
    assert order(c, q.id, "k1").json()["error"]["code"] == "QUOTE_EXPIRED"
    assert order_count() == 0


def test_superseded_quote_is_refused_as_changed(c):
    q = quote(fx.CAND_1)
    deps.valio.price["463038"] = 1800
    assert order(c, q.id, "k1").json()["status"] == "REQUOTE_REQUIRED" and quotes.get(q.id).status == "SUPERSEDED"
    assert order(c, q.id, "k2").json()["error"]["code"] == "QUOTE_CHANGED"
    assert order_count() == 1


def test_quote_cannot_be_ordered_under_another_protocol(c):
    q = quote(fx.CAND_1)                                                          # issued under prot_hotel_v1
    r = order(c, q.id, "k1", headers=EXT_AGENT, protocol_id=EXT)
    assert r.status_code == 403 and r.json()["error"]["code"] == "OUT_OF_SCOPE"
    assert quotes.get(q.id).status == "VALID" and order_count() == 0


# ───── P1: external authorization ─────

def test_price_change_after_authorization_needs_a_new_quote_and_a_new_token(c):
    q = quote(fx.CAND_3, EXT)
    o = order(c, q.id, "k1", headers=EXT_AGENT, protocol_id=EXT).json()
    assert o["status"] == "BUYER_AUTHORIZATION_REQUIRED"
    deps.valio.price["464201"] = 99
    moved = authorize(c, o["id"], token(q)).json()                                # a valid token does not lock the price
    assert moved["status"] == "REQUOTE_REQUIRED" and moved["new_quote_id"] not in (None, q.id)

    new = quotes.get(moved["new_quote_id"])
    o2 = order(c, new.id, "k2", headers=EXT_AGENT, protocol_id=EXT).json()
    assert o2["status"] == "BUYER_AUTHORIZATION_REQUIRED" and o2["authorization_request"]["quote_id"] == new.id
    stale = authorize(c, o2["id"], token(q))                                      # the token signed for the old quote
    assert stale.status_code == 403 and stale.json()["error"]["code"] == "AUTHORIZATION_INVALID"
    assert stale.json()["error"]["details"]["reason"] == "QUOTE_MISMATCH"
    assert authorize(c, o2["id"], token(new)).json()["status"] == "PLACED"


@pytest.mark.parametrize("candidate, status", [(fx.CAND_1, "PLACED"), (fx.CAND_3, "PENDING_BUYER_APPROVAL")],
                         ids=["placed", "hosted_approval"])
def test_authorization_is_refused_when_the_order_is_not_waiting_for_one(c, candidate, status):
    q = quote(candidate)
    o = order(c, q.id, "k1", headers=HOTEL_AGENT).json()
    assert o["status"] == status
    r = authorize(c, o["id"], token(q), headers=HOTEL_AGENT)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert c.get(f"/v1/orders/{o['id']}", headers=CHEF).json()["status"] == status


# ───── P1: stock ─────

def test_partial_stock_needs_a_new_quote_that_hits_the_partial_delivery_rule(c):
    q = quote(fx.CAND_1)
    deps.valio.stock["463038"] = AvailabilityInfo("PARTIAL", 1)
    o = order(c, q.id, "k1").json()
    assert o["status"] == "REQUOTE_REQUIRED" and quotes.get(q.id).status == "SUPERSEDED"
    new = quotes.get(o["new_quote_id"])
    assert new.lines[0].available_on_date == "PARTIAL" and new.lines[0].deliverable_sales_units == 1
    o2 = order(c, new.id, "k2").json()
    assert "R-DELIVERY-PARTIAL" in [h["rule_id"] for h in o2["gate"]["rules_hit"]]
    assert o2["status"] == "PENDING_BUYER_APPROVAL"                                # was AUTO while the stock was there


# ───── P1: approve with changes ─────

def test_buyer_approver_switching_to_the_bulk_candidate_sends_the_order_to_valio(c):
    # the price on the candidate is the one the approver saw; it must match the live price for the order to continue
    db.run("UPDATE candidates SET body_json = json_set(body_json, '$.lines[0].unit_price_cents', ?) WHERE id = 'cand_bulk'",
           (deps.valio.price["BULK"],))
    o = order(c, quote(fx.CAND_3).id, "k1").json()
    r = decide(c, o["approval_id"], MANAGER, AWC, changes={"candidate_id": "cand_bulk"}, note="Take the bulk offer").json()
    assert r["approval"]["status"] == "APPROVED_WITH_CHANGES" and r["order"]["status"] == "PENDING_VALIO_REVIEW"
    assert quotes.get(r["order"]["quote_id"]).candidate_id == "cand_bulk"
    assert r["order"]["approval_id"] not in (None, o["approval_id"])
    cards = c.get("/v1/approvals", params={"kind": "VALIO", "status": "PENDING"}, headers=VALIO).json()
    assert [(x["id"], x["reason_codes"]) for x in cards] == [(r["order"]["approval_id"], ["R-OUT-OF-API"])]


IN_BAND = {"product_id": "BULK", "unit_price_cents": 42}                           # accepted on its own, see the counter-offer test


@pytest.mark.parametrize("candidate, approver, bad", [
    (fx.CAND_3, MANAGER, {"candidate_id": "cand_nope"}),
    (BULK, VALIO, {"lines": [IN_BAND], "valid_minutes": 0}),
    (BULK, VALIO, {"lines": [IN_BAND], "valid_minutes": 1441}),                    # max_valid_minutes is 1440
], ids=["unknown_candidate", "zero_valid_minutes", "valid_minutes_over_the_maximum"])
def test_invalid_change_is_refused_and_changes_nothing(c, candidate, approver, bad):
    o = order(c, quote(candidate).id, "k1").json()
    r = decide(c, o["approval_id"], approver, AWC, changes=bad, note="x")
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert c.get(f"/v1/approvals/{o['approval_id']}", headers=approver).json()["status"] == "PENDING"
    after = c.get(f"/v1/orders/{o['id']}", headers=CHEF).json()
    assert (after["status"], after["quote_id"]) == (o["status"], o["quote_id"])
    assert quotes.get(o["quote_id"]).status == "VALID"


def test_counter_offer_may_be_valid_for_the_maximum_window(c):
    o = order(c, quote(BULK).id, "k1").json()
    r = decide(c, o["approval_id"], VALIO, AWC, changes={"lines": [IN_BAND], "valid_minutes": 1440}, note="Valid for a day").json()
    assert r["approval"]["status"] == "APPROVED_WITH_CHANGES"
    offer = quotes.get(r["order"]["new_quote_id"])
    assert timedelta(minutes=1439) < offer.valid_until - db.now() <= timedelta(minutes=1440)


def test_buyer_approver_changes_the_delivery_date(c):
    o = order(c, quote(fx.CAND_3).id, "k1").json()
    later = fx.CAND_3.delivery_date + timedelta(days=1)
    r = decide(c, o["approval_id"], MANAGER, AWC, changes={"delivery_date": later.isoformat()}, note="A day later is fine").json()
    assert r["approval"]["status"] == "APPROVED_WITH_CHANGES" and r["order"]["status"] == "PLACED"
    new = quotes.get(r["order"]["quote_id"])
    assert new.id != o["quote_id"] and new.delivery_date == later
    diff = [e for e in audit.entries() if e["event"] == "PROPOSAL_MODIFIED"][0]["payload"]["diff"]
    assert [x["field"] for x in diff] == ["delivery_date", "totals.gross_cents"]   # the new total is always recorded


@pytest.mark.parametrize("cents, status", [(44, "APPROVED_WITH_CHANGES"), (36, "APPROVED_WITH_CHANGES"),
                                           (45, "ROUTED"), (35, "ROUTED")])
def test_reviewer_price_band_includes_its_edge(c, cents, status):
    """The quote says 40 and the band is 10 %: 36 and 44 are still the reviewer's call, one cent further escalates."""
    o = order(c, quote(BULK).id, "k1").json()
    r = decide(c, o["approval_id"], VALIO, AWC, note="Edge of the band",
               changes={"lines": [{"product_id": "BULK", "unit_price_cents": cents}]}).json()
    assert r["approval"]["status"] == status
    assert quotes.get(o["quote_id"]).status == ("VALID" if status == "ROUTED" else "SUPERSEDED")


# ───── P1: protocol change approvals ─────

def test_rejected_protocol_change_revokes_the_new_version_only(c):
    r = decide(c, expansion(c), MANAGER, "REJECT", note="Not needed").json()
    assert r["approval"]["status"] == "REJECTED" and r["order"] is None
    assert protocols.get("prot_hotel_v2").status == "REVOKED"
    assert protocols.get_active("prot_hotel_v1", "agt_hotel").version == 1        # the current version keeps working


def test_protocol_change_cannot_be_approved_with_changes(c):
    approval_id = expansion(c)
    r = decide(c, approval_id, MANAGER, AWC, note="Half of it",
               changes={"lines": [{"product_id": "464201", "sales_units": 900}]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert c.get(f"/v1/approvals/{approval_id}", headers=MANAGER).json()["status"] == "PENDING"
    assert protocols.get("prot_hotel_v2").status == "PENDING_BUYER_SIGNATURE"


def test_routed_protocol_change_leaves_the_new_version_unsigned(c):
    r = decide(c, expansion(c), MANAGER, "ROUTE", note="For the owner").json()
    assert r["approval"]["status"] == "ROUTED"
    assert protocols.get("prot_hotel_v2").status == "PENDING_BUYER_SIGNATURE"


# ───── P1: protocols ─────

def test_active_protocol_cannot_be_edited(c):
    before = protocols.get("prot_hotel_v1")
    body = {**before.body.model_dump(mode="json"), "auto_approve_limit_cents": 1}
    r = c.put("/v1/protocols/prot_hotel_v1", json={"body": body}, headers=MANAGER)
    assert r.status_code == 403 and r.json()["error"]["code"] == "PROTOCOL_INVALID"
    assert protocols.get("prot_hotel_v1") == before


def test_protocol_past_its_valid_until_expires_on_use(c):
    q = quote(fx.CAND_1)
    db.run("UPDATE protocols SET body_json = json_set(body_json, '$.valid_until', '2020-01-01') WHERE id = 'prot_hotel_v1'")
    with pytest.raises(ApiError) as e:
        protocols.get_active("prot_hotel_v1", "agt_hotel")
    assert e.value.code == "PROTOCOL_INVALID" and protocols.get("prot_hotel_v1").status == "EXPIRED"
    assert order(c, q.id, "k1").json()["error"]["code"] == "PROTOCOL_INVALID"


def test_valio_can_revoke_any_protocol(c):
    for protocol_id in ("prot_hotel_v1", "prot_bistro_v1"):
        assert c.post(f"/v1/protocols/{protocol_id}/revoke", headers=VALIO).json()["status"] == "REVOKED"


def test_other_buyer_cannot_read_a_protocol(c):
    assert c.get("/v1/protocols/prot_hotel_v1", headers=MANAGER).json()["id"] == "prot_hotel_v1"
    r = c.get("/v1/protocols/prot_hotel_v1", headers=OWNER)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


# ───── P1: rate limit ─────

def test_rate_limit_window_ends_after_sixty_seconds(c, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(checkpoints.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(checkpoints, "_calls", defaultdict(deque))                # other tests leave calls behind
    p = protocols.get("prot_hotel_v1")
    agent = Audience(kind="AGENT", buyer_id="buy_hotel", agent_id="agt_hotel")
    for _ in range(p.body.valio_scope.rate_limit_per_min):
        checkpoints.check_query(agent, p, ["463038"])
    clock[0] += 60                                                                # exactly 60 s: still inside the window
    with pytest.raises(ApiError) as e:
        checkpoints.check_query(agent, p, ["463038"])
    assert e.value.code == "RATE_LIMITED"
    clock[0] += 1
    checkpoints.check_query(agent, p, ["463038"])


# ───── P2: hold report ─────

def test_report_with_since_after_until_is_empty(c):
    """Pins today's behaviour: no error, an empty report. A 422 would be the better answer (see the notes)."""
    order(c, quote(fx.CAND_3).id, "k1")                                           # a hold that exists right now
    assert holds(c, MANAGER).json()["total"] == 1
    r = holds(c, MANAGER, since="2026-02-01T00:00:00", until="2026-01-01T00:00:00")
    assert r.status_code == 200 and r.json()["total"] == 0 and r.json()["items"] == []


def test_report_defaults_to_the_seven_days_before_until(c, monkeypatch):
    now = db.now()
    for days in (8, 6):
        monkeypatch.setattr(db, "now", lambda d=days: now - timedelta(days=d))
        audit.append("ORDER_HELD", "system", {"days_ago": days}, buyer_id="buy_hotel", reason_code="R-SPEND-APPROVAL")
    monkeypatch.setattr(db, "now", lambda: now)
    r = holds(c, MANAGER, until=now.isoformat()).json()
    assert datetime.fromisoformat(r["until"]) - datetime.fromisoformat(r["since"]) == timedelta(days=7)
    assert r["total"] == 1 and datetime.fromisoformat(r["items"][0]["at"]) == now - timedelta(days=6)


# ───── P2: audit chain ─────

def chain_of_four():
    """Four entries at the end of the log, and how many intact entries come before the second one."""
    seqs = [audit.append("NOTE", "system", {"n": n}) for n in range(4)]
    return seqs, db.one("SELECT COUNT(*) AS n FROM audit_log WHERE seq < ?", (seqs[1],))["n"]


def test_deleted_audit_entry_breaks_the_chain_at_the_next_one(c):
    (_, second, third, _), intact = chain_of_four()
    db.run("DELETE FROM audit_log WHERE seq = ?", (second,))
    assert c.get("/v1/audit/verify").json() == {"ok": False, "entries_checked": intact, "first_bad_seq": third}


def test_swapped_audit_entries_break_the_chain_at_the_first_of_them(c):
    (_, second, third, _), intact = chain_of_four()
    for old, new in ((second, -1), (third, second), (-1, third)):
        db.run("UPDATE audit_log SET seq = ? WHERE seq = ?", (new, old))
    assert c.get("/v1/audit/verify").json() == {"ok": False, "entries_checked": intact, "first_bad_seq": second}


# ───── P2: approval cards ─────

def test_valio_card_with_a_high_risk_suggests_routing(c):
    o = order(c, quote(BULK).id, "k1").json()
    card = c.get(f"/v1/approvals/{o['approval_id']}", headers=VALIO).json()
    assert [(t["kind"], t["level"]) for t in card["risk_tags"]] == [("DELIVERY", "HIGH")]       # R-OUT-OF-API
    assert card["suggested_action"] == "ROUTE" and card["suggested_reason"].startswith("High risk")


def test_buyer_card_with_a_high_risk_suggests_rejecting(c):
    q = quote(fx.CAND_3)
    db.run("UPDATE quotes SET body_json = json_set(body_json, '$.internal.data_unverified', json('true')) WHERE id = ?", (q.id,))
    o = order(c, q.id, "k1").json()
    card = c.get(f"/v1/approvals/{o['approval_id']}", headers=MANAGER).json()
    assert "R-DATA-UNVERIFIED" in card["reason_codes"] and ("ALLERGEN", "HIGH") in [(t["kind"], t["level"]) for t in card["risk_tags"]]
    assert card["suggested_action"] == "REJECT" and card["suggested_reason"].startswith("High risk")


def test_card_without_a_high_risk_suggests_approving(c):
    o = order(c, quote(fx.CAND_3).id, "k1").json()
    card = c.get(f"/v1/approvals/{o['approval_id']}", headers=MANAGER).json()
    assert [t["level"] for t in card["risk_tags"]] == ["MEDIUM"] and card["suggested_action"] == "APPROVE"


def test_agent_deciding_a_valio_approval_gets_not_found(c):
    """Pins today's behaviour: the Valio queue is invisible to the buyer side, so the 404 comes first and the
    NOT_APPROVER branch for non-Valio callers in approvals.decide is never reached."""
    o = order(c, quote(BULK).id, "k1").json()
    r = decide(c, o["approval_id"], HOTEL_AGENT)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert c.get(f"/v1/approvals/{o['approval_id']}", headers=VALIO).json()["status"] == "PENDING"
