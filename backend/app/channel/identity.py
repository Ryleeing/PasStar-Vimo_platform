"""Identity headers: agents send X-Agent-Key, people in the UI send X-Demo-User (demo only, no login)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

from .. import db
from ..errors import ApiError
from ..internal import Audience


@dataclass
class Caller(Audience):
    """The Audience of an HTTP request plus the UI user behind it (BE1 needs to know who signs and who approves)."""
    user_id: Optional[str] = None


def key_hash(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def caller(agent_key: Optional[str], demo_user: Optional[str]) -> Caller:
    if agent_key:
        a = db.one("SELECT * FROM agents WHERE api_key_hash = ?", (key_hash(agent_key),))
        if a is None or a["status"] != "ACTIVE":
            raise ApiError("PROTOCOL_INVALID", "Unknown or blocked agent key")
        # 'last used' on the agents page (agent_connections, see channel/agents.py)
        db.run("UPDATE agent_connections SET last_used_at = ? WHERE agent_id = ?", (db.now().isoformat(), a["id"]))
        return Caller(kind="AGENT", buyer_id=a["buyer_id"], agent_id=a["id"])
    u = db.one("SELECT * FROM users WHERE id = ?", (demo_user,))
    if u is None:
        raise ApiError("OUT_OF_SCOPE", "Send X-Agent-Key or a known X-Demo-User")
    if u["org"] == "VALIO":
        return Caller(kind="VALIO_USER", buyer_id=None, agent_id=None, ceiling="L2", user_id=u["id"])
    return Caller(kind="BUYER_USER", buyer_id=u["buyer_id"], agent_id=None, user_id=u["id"])


def actor(a: Audience) -> str:
    """The actor string used in the audit log."""
    if a.kind == "AGENT":
        return f"agent:{a.agent_id}"
    return f"user:{a.user_id if isinstance(a, Caller) else a.buyer_id}"
