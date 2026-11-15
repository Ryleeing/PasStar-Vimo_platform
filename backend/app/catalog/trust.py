"""Step 0: deciding trust grades A–D (called by BE2 during preprocessing and card generation).

Rules (technical design document, "Deciding trust grades A–D"):
  A verified:   manufacturer's structured data, updated within 24 months, no conflicts
  B weaker:     structured but older than 24 months; or an allergen list with no "free from" declaration for this item
  C inferred:   only visible from the product name (and no structured data to check against)
  D missing/conflict: no data at all; or the name conflicts with the structured data
A hard requirement only counts as met when "explicitly met + grade A"; explicitly contains / may contain → violation (blocked);
structured data exists but lacks this declaration → no match (not a candidate, not blocked); everything else → unverified (sent to approval).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Optional

FRESH_DAYS = 730  # 24 months

# Claims recognisable in the name (Finnish / English / Swedish). Hints only, grade C at best.
NAME_PATTERNS = {
    "FREE_FROM_LACTOSE": r"laktoositon|lactose[- ]?free|laktosfri",
    "FREE_FROM_GLUTEN": r"gluteeniton|gluten[- ]?free|glutenfri",
    "VEGAN": r"vegaaninen|\bvegan\b|vegansk",
    "FREE_FROM_MILK": r"maidoton|milk[- ]?free|mjölkfri",
}

# Allergen codes for each claim: when the claim is missing, the allergen data fills in
CLAIM_TO_ALLERGEN = {"FREE_FROM_LACTOSE": "ML", "FREE_FROM_GLUTEN": "AW", "FREE_FROM_MILK": "AM",
                     "FREE_FROM_EGGS": "AE", "FREE_FROM_SOY": "AY"}


@dataclass
class ProductFacts:
    product_id: str
    has_synkka: bool
    synkka_updated_at: Optional[date]
    structured_claims: set[str] = field(default_factory=set)
    allergen_list_present: bool = False
    allergens: dict[str, str] = field(default_factory=dict)   # GS1 code -> CONTAINS | MAY_CONTAIN | FREE_FROM
    name_claims: set[str] = field(default_factory=set)
    has_claim_data: bool = False                               # whether the structured claim group exists


@dataclass
class RequirementCheck:
    kind: Literal["CLAIM", "ALLERGEN_FREE"]
    code: str
    status: Literal["CLAIMED", "NOT_CLAIMED", "FREE_FROM", "MAY_CONTAIN", "CONTAINS", "UNDECLARED"]
    grade: Literal["A", "B", "C", "D"]
    verdict: Literal["SATISFIED", "VIOLATED", "UNVERIFIED", "NOT_MATCHED"]
    source: str
    conflict: bool = False


def _fresh(f: ProductFacts, today: date) -> bool:
    return f.synkka_updated_at is not None and (today - f.synkka_updated_at).days <= FRESH_DAYS


def check_claim(f: ProductFacts, code: str, today: date) -> RequirementCheck:
    if f.has_synkka and code in f.structured_claims:
        g = "A" if _fresh(f, today) else "B"
        return RequirementCheck("CLAIM", code, "CLAIMED", g, "SATISFIED" if g == "A" else "UNVERIFIED",
                                "Manufacturer structured claim" + ("" if g == "A" else " (not updated for over 24 months)"))
    alg = CLAIM_TO_ALLERGEN.get(code)
    if alg and f.allergens.get(alg) in ("CONTAINS", "MAY_CONTAIN"):
        g = "A" if _fresh(f, today) else "B"
        clash = code in f.name_claims
        return RequirementCheck("CLAIM", code, f.allergens[alg], g, "VIOLATED",
                                f"Manufacturer declares it contains {alg}" + (", but the product name says free from: data conflict" if clash else ""), conflict=clash)
    if alg and f.allergens.get(alg) == "FREE_FROM":
        g = "A" if _fresh(f, today) else "B"
        return RequirementCheck("CLAIM", code, "FREE_FROM", g, "SATISFIED" if g == "A" else "UNVERIFIED",
                                f"Manufacturer explicitly declares free from {alg}")
    if code in f.name_claims:
        if f.has_synkka and f.has_claim_data:   # structured claims exist but not this one: name conflicts with data
            return RequirementCheck("CLAIM", code, "NOT_CLAIMED", "D", "UNVERIFIED",
                                    "Named in the product name, but missing from the manufacturer's structured claims", conflict=True)
        return RequirementCheck("CLAIM", code, "CLAIMED", "C", "UNVERIFIED", "From the product name only")
    if f.has_synkka and f.has_claim_data:
        g = "A" if _fresh(f, today) else "B"
        return RequirementCheck("CLAIM", code, "NOT_CLAIMED", g, "NOT_MATCHED", "Not among the manufacturer's claims")
    return RequirementCheck("CLAIM", code, "NOT_CLAIMED", "D", "UNVERIFIED", "No data available")


def check_allergen_free(f: ProductFacts, code: str, today: date) -> RequirementCheck:
    st = f.allergens.get(code)
    if st in ("CONTAINS", "MAY_CONTAIN"):
        g = "A" if _fresh(f, today) else "B"   # old data saying "contains" still excludes: better too strict than too loose
        return RequirementCheck("ALLERGEN_FREE", code, st, g, "VIOLATED", "Manufacturer allergen declaration")
    if st == "FREE_FROM":
        g = "A" if _fresh(f, today) else "B"
        return RequirementCheck("ALLERGEN_FREE", code, "FREE_FROM", g, "SATISFIED" if g == "A" else "UNVERIFIED",
                                "Manufacturer explicitly declares free from" + ("" if g == "A" else " (not updated for over 24 months)"))
    if f.has_synkka and f.allergen_list_present:
        return RequirementCheck("ALLERGEN_FREE", code, "UNDECLARED", "B", "UNVERIFIED",
                                "Allergen list exists, but this allergen is not declared absent")
    return RequirementCheck("ALLERGEN_FREE", code, "UNDECLARED", "D", "UNVERIFIED", "No allergen data")


def check(f: ProductFacts, kind: str, code: str, today: date) -> RequirementCheck:
    return check_claim(f, code, today) if kind == "CLAIM" else check_allergen_free(f, code, today)


def facts_from_raw(p: dict) -> ProductFacts:
    """Turn one raw Valio product JSON into ProductFacts. Used directly by preprocess.py."""
    s = p.get("synkkaData") or {}
    has = bool(s.get("gtin") or s.get("lastUpdated"))
    groups = {c["name"]: c.get("values") or [] for c in s.get("classifications") or []}
    allergens: dict[str, str] = {}
    for v in groups.get("allergen", []):
        if v.get("unit") in ("CONTAINS", "MAY_CONTAIN"):
            allergens[v["id"]] = v["unit"]
    for v in groups.get("nonAllergen", []):
        if v.get("unit") == "FREE_FROM" and v["id"] not in allergens:
            allergens[v["id"]] = "FREE_FROM"
    claims = {v["id"] for v in groups.get("nutritionalClaim", [])}
    names = " ".join(n["value"] for n in s.get("names") or [])
    name_claims = {k for k, pat in NAME_PATTERNS.items() if re.search(pat, names, re.I)}
    upd = s.get("lastUpdated")
    return ProductFacts(
        product_id=p["id"], has_synkka=has,
        synkka_updated_at=date.fromisoformat(upd[:10]) if upd else None,
        structured_claims=claims, allergen_list_present=bool(groups.get("allergen") or groups.get("nonAllergen")),
        allergens=allergens, name_claims=name_claims, has_claim_data=bool(claims),
    )
