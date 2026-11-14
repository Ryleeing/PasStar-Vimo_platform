"""Profile CRUD against the real schema, and protocol merge that only narrows."""
import sqlite3
from pathlib import Path

import pytest

from app import fixtures as fx
from app.orchestrator.profiles import ProfileRepo, merge_with_protocol
from app.schemas import HardExclusions, ProfileBody, ProfileDefaults, Requirement, SubstitutionPolicy

SCHEMA = Path(__file__).resolve().parents[1] / "backend" / "app" / "schema.sql"
NUT_FREE = Requirement(kind="ALLERGEN_FREE", code="AN", label="Nut-free")


@pytest.fixture
def repo():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA.read_text())
    conn.execute("INSERT INTO buyers (id, name) VALUES ('buy_hotel', 'Hotel Helsinki Central')")
    return ProfileRepo(conn)


# ───── CRUD ─────

def test_create_then_get(repo):
    p = repo.create("buy_hotel", fx.PROFILE.body)
    assert p.id.startswith("prof_") and p.buyer_id == "buy_hotel"
    assert repo.get(p.id).body == fx.PROFILE.body


def test_list_is_per_buyer_and_sorted(repo):
    repo.conn.execute("INSERT INTO buyers (id, name) VALUES ('buy_hospital', 'Hospital kitchen')")
    repo.create("buy_hotel", ProfileBody(name="Room service"))
    repo.create("buy_hotel", ProfileBody(name="Banquets"))
    repo.create("buy_hospital", ProfileBody(name="Wards"))
    assert [p.body.name for p in repo.list("buy_hotel")] == ["Banquets", "Room service"]


def test_update_changes_body_and_timestamp(repo):
    p = repo.create("buy_hotel", fx.PROFILE.body)
    new = fx.PROFILE.body.model_copy(update={"name": "Banquets v2"})
    updated = repo.update(p.id, new)
    assert updated.body.name == "Banquets v2" and updated.updated_at >= p.updated_at


def test_missing_profile(repo):
    assert repo.get("prof_nope") is None
    assert repo.update("prof_nope", ProfileBody(name="x")) is None
    assert repo.delete("prof_nope") is False


def test_delete(repo):
    p = repo.create("buy_hotel", ProfileBody(name="Temp"))
    assert repo.delete(p.id) and repo.get(p.id) is None


# ───── Merge ─────

def test_no_profile_keeps_protocol():
    rules = merge_with_protocol(fx.PROTOCOL.body, None)
    assert rules.protocol.substitution == fx.PROTOCOL.body.substitution
    assert rules.hard_requirements == fx.PROTOCOL.body.hard_requirements
    assert rules.excluded_brands == []


def test_hard_requirements_are_union_without_duplicates():
    body = ProfileBody(name="Wards", hard_exclusions=HardExclusions(requirements=[NUT_FREE, fx.LACTOSE_FREE]))
    rules = merge_with_protocol(fx.PROTOCOL.body, body)
    assert [(r.kind, r.code) for r in rules.hard_requirements] == [("CLAIM", "FREE_FROM_LACTOSE"), ("ALLERGEN_FREE", "AN")]
    assert rules.protocol.hard_requirements == rules.hard_requirements


def test_amounts_always_come_from_protocol():
    rules = merge_with_protocol(fx.PROTOCOL.body, fx.PROFILE.body)
    assert (rules.auto_limit_cents, rules.max_cents) == (100_000, 300_000)


def test_profile_exclusions_and_weights_pass_through():
    rules = merge_with_protocol(fx.PROTOCOL.body, fx.PROFILE.body)
    assert rules.excluded_brands == ["Arla"]
    assert rules.weights["fulfilment"] == 0.5
    assert rules.soft["prefer_large_packs"] is True


@pytest.mark.parametrize("choice, expected", [
    ("NONE", ("FORBIDDEN", "FORBIDDEN", "FORBIDDEN")),
    ("SAME_CATEGORY", ("AUTO", "APPROVAL", "FORBIDDEN")),
    ("ANY", ("AUTO", "APPROVAL", "APPROVAL")),
])
def test_substitution_takes_stricter_side(choice, expected):
    body = ProfileBody(name="p", defaults=ProfileDefaults(substitution=choice))
    s = merge_with_protocol(fx.PROTOCOL.body, body).protocol.substitution
    assert (s.official, s.same_category, s.cross_category) == expected


def test_profile_can_never_loosen_substitution():
    strict = fx.PROTOCOL.body.model_copy(update={"substitution": SubstitutionPolicy(
        official="FORBIDDEN", same_category="FORBIDDEN", cross_category="FORBIDDEN")})
    body = ProfileBody(name="p", defaults=ProfileDefaults(substitution="ANY"))
    s = merge_with_protocol(strict, body).protocol.substitution
    assert (s.official, s.same_category, s.cross_category) == ("FORBIDDEN", "FORBIDDEN", "FORBIDDEN")


def test_merge_does_not_mutate_inputs():
    before = fx.PROTOCOL.body.model_copy(deep=True)
    merge_with_protocol(fx.PROTOCOL.body, ProfileBody(name="p", hard_exclusions=HardExclusions(requirements=[NUT_FREE]),
                                                      defaults=ProfileDefaults(substitution="NONE")))
    assert fx.PROTOCOL.body == before
