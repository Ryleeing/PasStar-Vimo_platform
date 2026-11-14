"""Read-only view of the BE3 tables (requests, candidates, profiles), exactly as schema.sql defines them.

BE1 needs three things from a request: who made it (the requester cannot approve their own order), what was asked
(the approval card) and the candidates the gateway produced (the card, and substitutes an approver may pick).
internal.py has no interface for this, so this file is the single place where BE1 reads those tables. It never writes."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from .. import db
from ..schemas import Candidate, Profile, StructuredRequest


@dataclass
class RequestView:
    id: str
    buyer_id: str
    created_by: str
    raw_text: Optional[str]
    structured: StructuredRequest
    profile: Optional[Profile]
    candidates: list[Candidate]          # selectable candidates of the current attempt, by rank


def get(request_id: str) -> RequestView:
    r = db.one("SELECT * FROM requests WHERE id = ?", (request_id,))
    p = db.one("SELECT * FROM profiles WHERE id = ?", (r["profile_id"],))
    rows = db.all("SELECT body_json FROM candidates WHERE request_id = ? AND attempt = ? AND selectable = 1 ORDER BY rank",
                  (request_id, r["current_attempt"]))
    return RequestView(
        id=r["id"], buyer_id=r["buyer_id"], created_by=r["created_by"], raw_text=r["raw_text"],
        structured=StructuredRequest.model_validate_json(r["structured_json"]),
        profile=p and Profile(id=p["id"], buyer_id=p["buyer_id"], body=json.loads(p["body_json"]), updated_at=p["updated_at"]),
        candidates=[Candidate.model_validate_json(c["body_json"]) for c in rows])


def buyer_of(request_id: str) -> str:
    return db.one("SELECT buyer_id FROM requests WHERE id = ?", (request_id,))["buyer_id"]
