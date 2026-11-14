"""Demo seed data for the BE3 tables: two profiles per buyer (design doc, "demo seed data").
Runs after the BE1 seed, which creates the buyers. Requests, chain nodes and candidates are cleared."""
from __future__ import annotations

from .. import db
from ..schemas import HardExclusions, ProfileBody, ProfileDefaults, RankingWeights, Requirement, SoftPreferences

TABLES = ["candidates", "chain_nodes", "requests", "profiles"]
NUTS = [Requirement(kind="ALLERGEN_FREE", code="AN", label="Tree nuts-free"),
        Requirement(kind="ALLERGEN_FREE", code="AP", label="Peanuts-free")]

PROFILES: list[tuple[str, str, ProfileBody]] = [
    ("prof_banquet", "buy_hotel", ProfileBody(
        name="Hotel banquets", hard_exclusions=HardExclusions(brands=["Arla"]),
        soft_preferences=SoftPreferences(countries=["fi"], prefer_large_packs=True),
        defaults=ProfileDefaults(delivery_address="Hotel Helsinki Central, loading dock", lead_days=2),
        weights=RankingWeights(price=0.3, fulfilment=0.5, carbon=0.1, local=0.1))),
    ("prof_room_service", "buy_hotel", ProfileBody(
        name="Room service", soft_preferences=SoftPreferences(countries=["fi"]),
        defaults=ProfileDefaults(lead_days=1), weights=RankingWeights(price=0.2, fulfilment=0.6, carbon=0.1, local=0.1))),
    ("prof_wards", "buy_hospital", ProfileBody(
        name="Hospital wards", hard_exclusions=HardExclusions(requirements=NUTS),
        defaults=ProfileDefaults(delivery_address="Hospital kitchen, gate B", lead_days=1, substitution="NONE"),
        weights=RankingWeights(price=0.2, fulfilment=0.7, carbon=0.05, local=0.05))),
    ("prof_hospital_cafe", "buy_hospital", ProfileBody(
        name="Hospital cafe", hard_exclusions=HardExclusions(requirements=NUTS),
        weights=RankingWeights(price=0.5, fulfilment=0.3, carbon=0.1, local=0.1))),
    ("prof_bistro_daily", "buy_bistro", ProfileBody(
        name="Daily lunch", defaults=ProfileDefaults(lead_days=1),
        weights=RankingWeights(price=0.6, fulfilment=0.3, carbon=0.05, local=0.05))),
    ("prof_bistro_weekend", "buy_bistro", ProfileBody(
        name="Weekend brunch", soft_preferences=SoftPreferences(labels=["EU_ORGANIC_FARMING"]),
        weights=RankingWeights(price=0.3, fulfilment=0.4, carbon=0.2, local=0.1))),
]


def reset() -> None:
    """Goes through app.db so that, inside POST /v1/admin/reset, it joins that route's transaction."""
    for t in TABLES:
        db.run(f"DELETE FROM {t}")
    at = db.now().isoformat()
    for profile_id, buyer_id, body in PROFILES:
        db.run("INSERT INTO profiles (id, buyer_id, name, body_json, updated_at) VALUES (?, ?, ?, ?, ?)",
               (profile_id, buyer_id, body.name, body.model_dump_json(), at))
