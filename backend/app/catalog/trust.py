"""第 0 步：可信等级 A–D 的判定（BE2 在预处理和生成卡片时调用）。

规则（技术设计文档“可信等级 A–D 的判定”）：
  A 已证实：制造商结构化数据，24 个月内更新，无冲突
  B 较弱：  结构化但超过 24 个月；或有过敏原列表但对这一项没有“不含”声明
  C 推断：  只能从商品名称看出来（且没有结构化数据可以对照）
  D 缺失/冲突：没有任何数据；或名称与结构化数据冲突
硬性要求只有在“明确满足 + 等级 A”时才算满足；明确含有 / 可能含有 → 违反（标红）；
有结构化数据但没有这项声明 → 不匹配（不进候选、不标红）；其余 → 未验证（转审批）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Optional

FRESH_DAYS = 730  # 24 个月

# 名称里能识别的声明（芬兰语 / 英语 / 瑞典语）。只作提示，最高 C 级。
NAME_PATTERNS = {
    "FREE_FROM_LACTOSE": r"laktoositon|lactose[- ]?free|laktosfri",
    "FREE_FROM_GLUTEN": r"gluteeniton|gluten[- ]?free|glutenfri",
    "VEGAN": r"vegaaninen|\bvegan\b|vegansk",
    "FREE_FROM_MILK": r"maidoton|milk[- ]?free|mjölkfri",
}

# 声明对应的过敏原代码：声明缺失时，用过敏原数据补充判断
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
    has_claim_data: bool = False                               # 结构化声明组是否存在


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
        if f.has_synkka and f.has_claim_data:   # 有结构化声明却没有这一条：名称与数据冲突
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
        g = "A" if _fresh(f, today) else "B"   # 说“含有”的旧数据也照样排除：宁严勿宽
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
    """把 Valio 原始商品 JSON（一条）转成 ProductFacts。preprocess.py 直接用。"""
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
