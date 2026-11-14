"""Profiles: CRUD on the `profiles` table, and merging a profile into a protocol.

Merge rule (design doc, "protocol and profile body"): hard requirements are the
union; amounts, categories and substitution take the stricter side. A profile can
never make anything looser than the protocol.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

from ..internal import EffectiveRules
from ..schemas import PolicyAction, Profile, ProfileBody, ProtocolBody, Requirement, SubstitutionChoice

# ───────────── CRUD ─────────────

def _row_to_profile(row: sqlite3.Row) -> Profile:
    return Profile(id=row["id"], buyer_id=row["buyer_id"],
                   body=ProfileBody.model_validate_json(row["body_json"]),
                   updated_at=datetime.fromisoformat(row["updated_at"]))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProfileRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        conn.row_factory = sqlite3.Row
        self.conn = conn

    def list(self, buyer_id: str) -> list[Profile]:
        rows = self.conn.execute(
            "SELECT * FROM profiles WHERE buyer_id = ? ORDER BY name", (buyer_id,)).fetchall()
        return [_row_to_profile(r) for r in rows]

    def get(self, profile_id: str) -> Optional[Profile]:
        row = self.conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        return _row_to_profile(row) if row else None

    def create(self, buyer_id: str, body: ProfileBody, profile_id: Optional[str] = None) -> Profile:
        pid = profile_id or f"prof_{uuid.uuid4().hex[:8]}"
        self.conn.execute(
            "INSERT INTO profiles (id, buyer_id, name, body_json, updated_at) VALUES (?, ?, ?, ?, ?)",
            (pid, buyer_id, body.name, body.model_dump_json(), _now()))
        self.conn.commit()
        return self.get(pid)

    def update(self, profile_id: str, body: ProfileBody) -> Optional[Profile]:
        cur = self.conn.execute(
            "UPDATE profiles SET name = ?, body_json = ?, updated_at = ? WHERE id = ?",
            (body.name, body.model_dump_json(), _now(), profile_id))
        self.conn.commit()
        return self.get(profile_id) if cur.rowcount else None

    def delete(self, profile_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        self.conn.commit()
        return cur.rowcount > 0


# ───────────── Merge with protocol (narrow only) ─────────────

_STRICTNESS = {"FORBIDDEN": 0, "APPROVAL": 1, "AUTO": 2}

# The loosest action each substitution tier may have under a profile's default choice.
_CHOICE_CAP: dict[SubstitutionChoice, dict[str, PolicyAction]] = {
    "NONE": {"official": "FORBIDDEN", "same_category": "FORBIDDEN", "cross_category": "FORBIDDEN"},
    "SAME_CATEGORY": {"official": "AUTO", "same_category": "AUTO", "cross_category": "FORBIDDEN"},
    "ANY": {"official": "AUTO", "same_category": "AUTO", "cross_category": "AUTO"},
}


def _stricter(a: PolicyAction, b: PolicyAction) -> PolicyAction:
    return a if _STRICTNESS[a] <= _STRICTNESS[b] else b


def _union_requirements(*groups: list[Requirement]) -> list[Requirement]:
    seen: set[tuple[str, str]] = set()
    out: list[Requirement] = []
    for group in groups:
        for r in group:
            if (r.kind, r.code) not in seen:
                seen.add((r.kind, r.code))
                out.append(r)
    return out


def merge_with_protocol(protocol: ProtocolBody, profile: Optional[ProfileBody]) -> EffectiveRules:
    """Combine a protocol and an optional profile into EffectiveRules. Only ever narrows.

    BE1's ProtocolsApi.effective_rules() is expected to call this."""
    if profile is None:
        profile = ProfileBody(name="(none)", defaults={"substitution": "ANY"})

    cap = _CHOICE_CAP[profile.defaults.substitution]
    substitution = protocol.substitution.model_copy(update={
        tier: _stricter(getattr(protocol.substitution, tier), cap[tier]) for tier in cap})
    hard = _union_requirements(protocol.hard_requirements, profile.hard_exclusions.requirements)
    narrowed = protocol.model_copy(update={"hard_requirements": hard, "substitution": substitution}, deep=True)

    return EffectiveRules(
        protocol=narrowed,
        hard_requirements=hard,
        excluded_brands=list(profile.hard_exclusions.brands),
        excluded_vendors=list(profile.hard_exclusions.vendors),
        excluded_product_ids=list(profile.hard_exclusions.product_ids),
        auto_limit_cents=protocol.auto_approve_limit_cents,
        max_cents=protocol.max_order_cents,
        weights=profile.weights.model_dump(),
        soft=profile.soft_preferences.model_dump(),
    )
