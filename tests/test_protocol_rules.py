from datetime import date

from app.channel.protocol_rules import apply_tightening, classify_changes
from app.fixtures import PROTOCOL

BODY = PROTOCOL.body
TODAY = date(2026, 11, 14)


def kinds(res):
    return {k: [c.field for c in v] for k, v in res.items() if v}


def test_lower_auto_limit_is_tightening():
    assert kinds(classify_changes(BODY, {"auto_approve_limit_cents": 80_000}, "buyer", TODAY)) == {"tightened": ["auto_approve_limit_cents"]}


def test_raise_auto_limit_is_expansion():
    assert kinds(classify_changes(BODY, {"auto_approve_limit_cents": 150_000}, "buyer", TODAY)) == {"expanded": ["auto_approve_limit_cents"]}


def test_auto_limit_above_max_is_forbidden():
    assert kinds(classify_changes(BODY, {"auto_approve_limit_cents": 400_000}, "buyer", TODAY)) == {"forbidden": ["auto_approve_limit_cents"]}


def test_categories_split_into_both():
    r = classify_changes(BODY, {"allowed_categories": ["10400", "22540"]}, "buyer", TODAY)
    assert kinds(r) == {"tightened": ["allowed_categories"], "expanded": ["allowed_categories"]}


def test_adding_requirement_is_tightening_removing_is_expansion():
    add = classify_changes(BODY, {"hard_requirements": [*BODY.hard_requirements, {"kind": "ALLERGEN_FREE", "code": "AN"}]}, "buyer", TODAY)
    rem = classify_changes(BODY, {"hard_requirements": []}, "buyer", TODAY)
    assert kinds(add) == {"tightened": ["hard_requirements"]}
    assert kinds(rem) == {"expanded": ["hard_requirements"]}


def test_substitution_direction():
    r = classify_changes(BODY, {"substitution": {"same_category": "AUTO", "cross_category": "FORBIDDEN"}}, "buyer", TODAY)
    assert kinds(r) == {"tightened": ["substitution.cross_category"], "expanded": ["substitution.same_category"]}


def test_buyer_cannot_widen_valio_scope_but_valio_can():
    assert kinds(classify_changes(BODY, {"valio_scope": {"rate_limit_per_min": 100}}, "buyer", TODAY)) == {"forbidden": ["valio_scope.rate_limit_per_min"]}
    assert kinds(classify_changes(BODY, {"valio_scope": {"rate_limit_per_min": 100}}, "valio", TODAY)) == {"expanded": ["valio_scope.rate_limit_per_min"]}
    assert kinds(classify_changes(BODY, {"valio_scope": {"rate_limit_per_min": 10}}, "buyer", TODAY)) == {"tightened": ["valio_scope.rate_limit_per_min"]}


def test_immutable_and_validity():
    assert kinds(classify_changes(BODY, {"agent_id": "agt_other"}, "buyer", TODAY)) == {"forbidden": ["agent_id"]}
    assert kinds(classify_changes(BODY, {"valid_until": "2028-01-01"}, "buyer", TODAY)) == {"forbidden": ["valid_until"]}
    assert kinds(classify_changes(BODY, {"valid_until": "2027-01-01"}, "buyer", TODAY)) == {"tightened": ["valid_until"]}


def test_apply_only_tightening():
    changes = {"auto_approve_limit_cents": 80_000, "allowed_categories": ["10400", "22540"]}
    res = classify_changes(BODY, changes, "buyer", TODAY)
    new = apply_tightening(BODY, changes, res)
    assert new.auto_approve_limit_cents == 80_000
    assert new.allowed_categories == BODY.allowed_categories  # 混合变化的字段等审批
