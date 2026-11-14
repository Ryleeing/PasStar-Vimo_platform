"""BE3 routes on the database: profiles, requests and selecting a candidate.

main.py stays thin; these functions do the work with the caller resolved by channel.identity.
Every response goes through checkpoints.check_response (the BE2 disclosure filter).

BE2 is not wired yet: catalogue search, unit conversion and cards use BE3's stubs; price, stock and the
disclosure filter use whatever BE2 registered in app.deps (BE3's stubs until then, see __init__.py).
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from typing import Iterator, Optional

from .. import db, deps
from ..channel import audit, checkpoints, gate, protocols, quotes
from ..errors import ApiError
from ..internal import Audience
from ..schemas import (
    Candidate, CreateRequest, Profile, ProfileBody, RejectNodeRequest, SelectRequest, SelectResult,
)
from .pipeline import AGENT_PREFIX, Deps, Pipeline
from .profiles import ProfileRepo
from .stubs import StubCards, StubCatalog, StubUnits


def _deps() -> Deps:
    from . import explain_api
    return Deps(catalog=StubCatalog(), units=StubUnits(), valio=deps.valio, cards=StubCards(),
                disclosure=deps.disclosure, protocols=protocols, gate=gate, explain=explain_api, audit=audit)


@contextmanager
def _pipeline() -> Iterator[Pipeline]:
    with closing(db.connect()) as conn:
        yield Pipeline(conn, _deps(), clock=db.now)


@contextmanager
def _profiles() -> Iterator[ProfileRepo]:
    with closing(db.connect()) as conn:
        yield ProfileRepo(conn)


def _out(who: Audience, model) -> dict:
    return checkpoints.check_response(who, model.model_dump(mode="json"))


def _require_buyer(who: Audience, buyer_id: str) -> None:
    if who.buyer_id != buyer_id:
        raise ApiError("OUT_OF_SCOPE", "This belongs to another buyer")


def _created_by(who: Audience) -> str:
    return f"{AGENT_PREFIX}{who.agent_id}" if who.kind == "AGENT" else who.user_id


def _request_row(request_id: str, who: Audience):
    row = db.one("SELECT * FROM requests WHERE id = ?", (request_id,))
    if row is None:
        raise ApiError("NOT_FOUND", f"Request {request_id} does not exist")
    if who.kind != "VALIO_USER":
        _require_buyer(who, row["buyer_id"])
    return row


def _active_protocol(protocol_id: str, who: Audience):
    return protocols.get_active(protocol_id, who.agent_id if who.kind == "AGENT" else None)


# ───── profiles ─────

def list_profiles(buyer_id: str, who: Audience) -> list[dict]:
    _require_buyer(who, buyer_id)
    with _profiles() as repo:
        return [_out(who, p) for p in repo.list(buyer_id)]


def create_profile(buyer_id: str, body: ProfileBody, who: Audience) -> dict:
    _require_buyer(who, buyer_id)
    with _profiles() as repo:
        return _out(who, repo.create(buyer_id, body))


def get_profile(profile_id: str, who: Audience) -> Profile:
    with _profiles() as repo:
        p = repo.get(profile_id)
    if p is None:
        raise ApiError("NOT_FOUND", f"Profile {profile_id} does not exist")
    _require_buyer(who, p.buyer_id)
    return p


def update_profile(profile_id: str, body: ProfileBody, who: Audience) -> dict:
    get_profile(profile_id, who)
    with _profiles() as repo:
        return _out(who, repo.update(profile_id, body))


def profile_for(profile_id: Optional[str], who: Audience) -> Optional[Profile]:
    """The caller's own profile, or None when none was asked for."""
    return get_profile(profile_id, who) if profile_id else None


# ───── requests ─────

def create_request(body: CreateRequest, who: Audience) -> dict:
    protocol = _active_protocol(body.protocol_id, who)
    checkpoints.check_query(who, protocol, [])
    if body.structured.category not in protocol.body.allowed_categories:
        # Refuse, rather than search and come back empty: the protocol does not allow this category at all.
        raise ApiError("OUT_OF_SCOPE", f"Category {body.structured.category} is not allowed by protocol {protocol.id}",
                       allowed_categories=protocol.body.allowed_categories)
    profile_for(body.profile_id, who)
    with _pipeline() as p:
        return _out(who, p.result(p.create_request(protocol.body.buyer_id, _created_by(who), body)))


def get_request(request_id: str, who: Audience, attempt: Optional[int] = None) -> dict:
    _request_row(request_id, who)
    with _pipeline() as p:
        return _out(who, p.result(request_id, attempt))


def reject_node(request_id: str, node: int, body: RejectNodeRequest, who: Audience) -> dict:
    row = _request_row(request_id, who)
    checkpoints.check_query(who, _active_protocol(row["protocol_id"], who), [])
    with _pipeline() as p:
        p.reject(request_id, node, body)
        return _out(who, p.result(request_id))


def chain(request_id: str, who: Audience, attempt: Optional[int] = None) -> dict:
    _request_row(request_id, who)
    with _pipeline() as p:
        return _out(who, p.timeline(request_id, attempt))


def select(request_id: str, body: SelectRequest, who: Audience) -> dict:
    """Quote the chosen candidate (BE1 signs it, 15 minutes) and run the binding gate decision."""
    row = _request_row(request_id, who)
    protocol = _active_protocol(row["protocol_id"], who)
    c = db.one("SELECT body_json FROM candidates WHERE id = ? AND request_id = ? AND attempt = ?",
               (body.candidate_id, request_id, row["current_attempt"]))
    if c is None:
        raise ApiError("NOT_FOUND", f"Candidate {body.candidate_id} is not in the latest attempt of {request_id}")
    candidate = Candidate.model_validate_json(c["body_json"])
    checkpoints.check_query(who, protocol, [l.product_id for l in candidate.lines])
    quote = quotes.create(request_id, candidate, protocol)          # refuses red-flagged candidates
    profile = profile_for(row["profile_id"], who) if row["profile_id"] else None
    decision = gate.evaluate(quote, protocols.effective_rules(protocol, profile))
    db.run("UPDATE requests SET status = 'QUOTED' WHERE id = ?", (request_id,))
    return _out(who, SelectResult(quote=quote, gate=decision))
