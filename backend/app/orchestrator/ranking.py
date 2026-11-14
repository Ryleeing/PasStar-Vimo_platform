"""Ranking candidates by profile weights and assigning the top-3 badges.

First version: single-SKU options only. Split orders and substitute combinations come later.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..schemas import CandidateLine, Totals

RELIABILITY_SCORE = {"HIGH": 1.0, "MEDIUM": 0.6, "LOW": 0.3, "UNKNOWN": 0.5}
RELIABILITY_ORDER = {"HIGH": 3, "MEDIUM": 2, "UNKNOWN": 1, "LOW": 0}


@dataclass
class Draft:
    """A candidate before cards and explanation are attached."""
    lines: list[CandidateLine]
    totals: Totals
    reliability_level: str
    rank: Optional[int] = None
    badge: Optional[str] = None
    score: float = 0.0
    extra_reasons: list[str] = field(default_factory=list)

    @property
    def product_ids(self) -> list[str]:
        return [l.product_id for l in self.lines]


def score(drafts: list[Draft], weights: dict[str, float]) -> None:
    cheapest = min(d.totals.gross_cents for d in drafts)
    for d in drafts:
        price = cheapest / d.totals.gross_cents if d.totals.gross_cents else 0
        d.score = (weights.get("price", 0) * price
                   + weights.get("fulfilment", 0) * RELIABILITY_SCORE[d.reliability_level]
                   + (weights.get("carbon", 0) + weights.get("local", 0)) * 0.5)


def deliverable(d: Draft) -> bool:
    """False when no line can be delivered on the requested date (the gate would reject it: R-DELIVERY-NONE)."""
    return any(line.available_on_date != "NO" for line in d.lines)


def rank(drafts: list[Draft], weights: dict[str, float], limit: int = 8) -> list[Draft]:
    """Order drafts and set rank/badge: #1 best score, #2 cheapest of the rest, #3 most reliable of the rest.

    Options that cannot be delivered on the requested date come last and never get a badge: they stay
    visible (the buyer sees why) but are never recommended."""
    for d in drafts:
        d.rank, d.badge = None, None
    if not drafts:
        return []
    score(drafts, weights)
    by_score = sorted(drafts, key=lambda d: (-d.score, d.totals.gross_cents))
    pool = [d for d in by_score if deliverable(d)]
    ordered: list[Draft] = []

    def take(d: Draft, badge: str) -> None:
        pool.remove(d)
        d.badge = badge
        ordered.append(d)

    if pool:
        take(pool[0], "RECOMMENDED")
    if pool:
        take(min(pool, key=lambda d: d.totals.gross_cents), "CHEAPEST")
    if pool:
        take(max(pool, key=lambda d: (RELIABILITY_ORDER[d.reliability_level], d.score)), "MOST_RELIABLE")
    ordered.extend(pool)
    ordered.extend(d for d in by_score if not deliverable(d))
    for i, d in enumerate(ordered[:limit], start=1):
        d.rank = i
    return ordered[:limit]
