from datetime import date

from app.catalog.trust import ProductFacts, check

T = date(2026, 11, 14)
FRESH, OLD = date(2025, 6, 1), date(2023, 1, 1)


def f(**kw):
    base = dict(product_id="x", has_synkka=True, synkka_updated_at=FRESH)
    base.update(kw)
    return ProductFacts(**base)


def v(facts, kind, code):
    r = check(facts, kind, code, T)
    return r.grade, r.verdict


def test_structured_fresh_claim_is_A_satisfied():
    assert v(f(structured_claims={"FREE_FROM_LACTOSE"}, has_claim_data=True), "CLAIM", "FREE_FROM_LACTOSE") == ("A", "SATISFIED")


def test_old_claim_is_B_unverified():
    assert v(f(synkka_updated_at=OLD, structured_claims={"FREE_FROM_LACTOSE"}, has_claim_data=True), "CLAIM", "FREE_FROM_LACTOSE") == ("B", "UNVERIFIED")


def test_missing_claim_with_data_is_not_matched_not_red():
    assert v(f(structured_claims={"LOW_FAT"}, has_claim_data=True), "CLAIM", "FREE_FROM_GLUTEN") == ("A", "NOT_MATCHED")


def test_allergen_backs_up_claim():
    assert v(f(allergens={"ML": "FREE_FROM"}, allergen_list_present=True), "CLAIM", "FREE_FROM_LACTOSE") == ("A", "SATISFIED")
    assert v(f(allergens={"ML": "CONTAINS"}, allergen_list_present=True), "CLAIM", "FREE_FROM_LACTOSE") == ("A", "VIOLATED")


def test_name_vs_contains_is_violated_with_conflict():
    r = check(f(allergens={"ML": "CONTAINS"}, allergen_list_present=True, name_claims={"FREE_FROM_LACTOSE"}), "CLAIM", "FREE_FROM_LACTOSE", T)
    assert (r.verdict, r.conflict) == ("VIOLATED", True)


def test_name_vs_structured_claims_is_D_conflict():
    r = check(f(structured_claims={"VEGETARIAN"}, has_claim_data=True, name_claims={"FREE_FROM_LACTOSE"}), "CLAIM", "FREE_FROM_LACTOSE", T)
    assert (r.grade, r.verdict, r.conflict) == ("D", "UNVERIFIED", True)


def test_name_only_without_data_is_C():
    assert v(f(has_synkka=False, synkka_updated_at=None, name_claims={"FREE_FROM_LACTOSE"}), "CLAIM", "FREE_FROM_LACTOSE") == ("C", "UNVERIFIED")


def test_no_data_is_D():
    assert v(f(has_synkka=False, synkka_updated_at=None), "ALLERGEN_FREE", "AN") == ("D", "UNVERIFIED")


def test_allergen_free_rules():
    assert v(f(allergens={"AN": "FREE_FROM"}, allergen_list_present=True), "ALLERGEN_FREE", "AN") == ("A", "SATISFIED")
    assert v(f(allergens={"AM": "CONTAINS"}, allergen_list_present=True), "ALLERGEN_FREE", "AN") == ("B", "UNVERIFIED")
    assert v(f(allergens={"AN": "MAY_CONTAIN"}, allergen_list_present=True), "ALLERGEN_FREE", "AN") == ("A", "VIOLATED")
    assert v(f(synkka_updated_at=OLD, allergens={"AN": "CONTAINS"}, allergen_list_present=True), "ALLERGEN_FREE", "AN") == ("B", "VIOLATED")
