"""Audit log (AuditApi): append-only with a hash chain.
hash = sha256(prev_hash + at + actor + event + payload_json); the first entry's prev_hash is 64 zeros.

payload_json holds {"summary": ..., "payload": ...}, so the summary is covered by the hash as well.
Payload keys that carry meaning for readers of the log: "buyer_id" (whose entry it is, for role filtering),
"agent_id", "reason_code" (why something was held) and "disclosed_fields" (a list of {"field", "sensitivity"}:
what was disclosed in this step). The first three are also stored as columns, for filtering; the columns are
copies of the hashed payload values and verify() checks that they still match."""
from __future__ import annotations

import hashlib
import json
from typing import Literal, Optional

from .. import db
from ..schemas import AuditVerifyResult

GENESIS = "0" * 64
COLUMNS = ("buyer_id", "agent_id", "reason_code")     # copies of the payload values with the same keys


def _hash(prev: str, at: str, actor: str, event: str, payload_json: str) -> str:
    return hashlib.sha256((prev + at + actor + event + payload_json).encode()).hexdigest()


def append(event: str, actor: str, payload: dict, request_id: Optional[str] = None,
           visibility: Literal["BUYER", "VALIO", "BOTH"] = "BOTH", summary: str = "",
           buyer_id: Optional[str] = None, agent_id: Optional[str] = None, reason_code: Optional[str] = None) -> int:
    given = {"buyer_id": buyer_id, "agent_id": agent_id, "reason_code": reason_code}
    payload = {**payload, **{k: v for k, v in given.items() if v is not None}}
    at = db.now().isoformat()
    payload_json = json.dumps({"summary": summary, "payload": payload}, sort_keys=True, separators=(",", ":"), default=str)
    with db.transaction():                    # reading the last hash and inserting must share one transaction
        last = db.one("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1")
        prev = last["hash"] if last else GENESIS
        return db.run(
            "INSERT INTO audit_log (at, request_id, actor, event, visibility, payload_json, prev_hash, hash,"
            " buyer_id, agent_id, reason_code) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (at, request_id, actor, event, visibility, payload_json, prev, _hash(prev, at, actor, event, payload_json),
             *(payload.get(k) for k in COLUMNS)))


def entries(request_id: Optional[str] = None) -> list[dict]:
    """All entries, not filtered by role; pass them through checkpoints.check_log before returning them."""
    where, params = ("WHERE request_id = ?", (request_id,)) if request_id else ("", ())
    out = []
    for r in db.all(f"SELECT * FROM audit_log {where} ORDER BY seq", params):
        body = json.loads(r["payload_json"])
        out.append({"seq": r["seq"], "at": r["at"], "request_id": r["request_id"], "actor": r["actor"], "event": r["event"],
                    "visibility": r["visibility"], "summary": body["summary"], "payload": body["payload"],
                    "disclosed_fields": body["payload"].get("disclosed_fields", []),
                    "prev_hash": r["prev_hash"], "hash": r["hash"]})
    return out


def verify() -> AuditVerifyResult:
    prev, n = GENESIS, 0
    for r in db.all("SELECT * FROM audit_log ORDER BY seq"):
        intact = r["prev_hash"] == prev and r["hash"] == _hash(prev, r["at"], r["actor"], r["event"], r["payload_json"])
        if not intact or any(r[k] != json.loads(r["payload_json"])["payload"].get(k) for k in COLUMNS):
            return AuditVerifyResult(ok=False, entries_checked=n, first_bad_seq=r["seq"])
        prev, n = r["hash"], n + 1
    return AuditVerifyResult(ok=True, entries_checked=n)
