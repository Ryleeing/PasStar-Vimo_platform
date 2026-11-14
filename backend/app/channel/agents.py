"""External agents and their keys: who is connected, how, under which protocol, and the key's lifecycle.

A key is the agent's credential ("work badge"): it says which agent is calling, and through the agent, which buyer
and which active protocol it acts under. PasStar stores only its hash, shows it once when issued, and a lost or
leaked key is replaced or revoked, never shown again. Only the approver named in the agent's active protocol may
issue or revoke its key, so an agent cannot be given access before step 0 (a signed, confirmed protocol).

Nothing is ever deleted: the audit log keeps referring to the agent. A buyer disconnects an agent by revoking its
key; Valio blocks an agent that misbehaves on its channel, which refuses it even with a valid key.
"""
from __future__ import annotations

import secrets
from typing import Optional

from .. import db
from ..errors import ApiError
from ..schemas import AgentConnection, IssuedKey
from . import audit, protocols
from .approvals import _activity
from .identity import Caller, actor, key_hash

CONNECTION = {"CHATGPT": "MCP", "CLAUDE": "MCP", "ERP": "REST", "UNKNOWN": "NONE"}
REVOKED = "revoked"          # api_key_hash of a revoked key: matches no key, since every key hashes to 64 hex chars


def _active_protocol(agent_id: str):
    r = db.one("SELECT id FROM protocols WHERE agent_id = ? AND status = 'ACTIVE' ORDER BY version DESC LIMIT 1",
               (agent_id,))
    return protocols.get_active(r["id"], None) if r else None


def _connection(row) -> AgentConnection:
    p = _active_protocol(row["id"])
    kind = row["kind"] or "UNKNOWN"
    return AgentConnection(
        agent_id=row["id"], name=row["name"], buyer_id=row["buyer_id"], buyer_name=row["buyer_name"],
        kind=kind, status=row["status"], connection=CONNECTION[kind] if p and row["status"] == "ACTIVE" else "NONE",
        has_key=row["api_key_hash"] != REVOKED and row["key_hint"] is not None, key_hint=row["key_hint"],
        key_issued_at=row["key_issued_at"], last_used_at=row["last_used_at"],
        protocol_id=p.id if p else None, protocol_version=p.version if p else None,
        approver=p.body.approver if p else None, activity=_activity(row["id"]))


_SELECT = ("SELECT a.*, b.name AS buyer_name, c.kind, c.key_hint, c.key_issued_at, c.last_used_at FROM agents a"
           " JOIN buyers b ON b.id = a.buyer_id LEFT JOIN agent_connections c ON c.agent_id = a.id")


def list_for(who: Caller) -> list[AgentConnection]:
    """Valio sees every agent; a buyer sees its own. Agents do not list agents."""
    if who.kind == "AGENT":
        raise ApiError("OUT_OF_SCOPE", "Agents cannot list agents")
    if who.kind == "VALIO_USER":
        rows = db.all(_SELECT + " ORDER BY b.name, a.id")
    else:
        rows = db.all(_SELECT + " WHERE a.buyer_id = ? ORDER BY a.id", (who.buyer_id,))
    return [_connection(r) for r in rows]


def get(agent_id: str, who: Caller) -> AgentConnection:
    row = db.one(_SELECT + " WHERE a.id = ?", (agent_id,))
    if row is None or (who.kind != "VALIO_USER" and who.buyer_id != row["buyer_id"]):
        raise ApiError("NOT_FOUND", f"Agent {agent_id} does not exist")
    return _connection(row)


def _may_manage(agent_id: str, who: Caller):
    """The approver of the agent's active protocol, and only them."""
    conn = get(agent_id, who)
    if who.kind != "BUYER_USER":
        raise ApiError("NOT_APPROVER", "Only the buyer's approver manages its agents' keys")
    p = _active_protocol(agent_id)
    if p is None:
        raise ApiError("PROTOCOL_INVALID", f"Agent {agent_id} has no active protocol; sign one first (step 0)")
    if conn.status == "BLOCKED":
        raise ApiError("OUT_OF_SCOPE", "Valio has blocked this agent; ask Valio to unblock it first")
    if p.body.approver != who.user_id:
        raise ApiError("NOT_APPROVER", "Only the approver named in the protocol can issue or revoke this key",
                       approver=p.body.approver)
    return conn, p


def issue_key(agent_id: str, kind: str, who: Caller) -> IssuedKey:
    """A new key for the agent; any earlier key stops working at once."""
    _, p = _may_manage(agent_id, who)
    key = f"tp_{secrets.token_urlsafe(24)}"
    store_key(agent_id, key, kind)
    audit.append("AGENT_KEY_ISSUED", actor(who), {"buyer_id": p.body.buyer_id, "agent_id": agent_id, "kind": kind,
                                                  "key_hint": key[:8], "protocol_id": p.id},
                 summary=f"Issued a new key to agent {agent_id} ({kind}); the previous key no longer works",
                 buyer_id=p.body.buyer_id, agent_id=agent_id)
    return IssuedKey(agent_id=agent_id, key=key, key_hint=key[:8], kind=kind, protocol_id=p.id)


def revoke_key(agent_id: str, who: Caller) -> AgentConnection:
    _, p = _may_manage(agent_id, who)
    db.run("UPDATE agents SET api_key_hash = ? WHERE id = ?", (REVOKED, agent_id))
    db.run("UPDATE agent_connections SET key_hint = NULL WHERE agent_id = ?", (agent_id,))
    audit.append("AGENT_KEY_REVOKED", actor(who), {"buyer_id": p.body.buyer_id, "agent_id": agent_id},
                 summary=f"Revoked the key of agent {agent_id}; it can no longer call PasStar",
                 buyer_id=p.body.buyer_id, agent_id=agent_id)
    return get(agent_id, who)


def set_blocked(agent_id: str, blocked: bool, who: Caller) -> AgentConnection:
    """Valio blocks (or unblocks) an agent on its channel. A blocked agent is refused even with a valid key."""
    if who.kind != "VALIO_USER":
        raise ApiError("NOT_APPROVER", "Only Valio blocks or unblocks agents")
    conn = get(agent_id, who)
    db.run("UPDATE agents SET status = ? WHERE id = ?", ("BLOCKED" if blocked else "ACTIVE", agent_id))
    audit.append("AGENT_BLOCKED_BY_VALIO" if blocked else "AGENT_UNBLOCKED_BY_VALIO", actor(who),
                 {"buyer_id": conn.buyer_id, "agent_id": agent_id},
                 summary=f"Valio {'blocked' if blocked else 'unblocked'} agent {agent_id}",
                 buyer_id=conn.buyer_id, agent_id=agent_id)
    return get(agent_id, who)


def store_key(agent_id: str, key: str, kind: Optional[str] = None) -> None:
    """Replace the agent's key (hash only) and record when, for the agents page. Also used by
    scripts/new_agent_keys.py."""
    db.run("UPDATE agents SET api_key_hash = ? WHERE id = ?", (key_hash(key), agent_id))
    db.run("INSERT INTO agent_connections (agent_id, kind, key_hint, key_issued_at) VALUES (?, ?, ?, ?)"
           " ON CONFLICT(agent_id) DO UPDATE SET kind = COALESCE(?, kind), key_hint = excluded.key_hint,"
           " key_issued_at = excluded.key_issued_at",
           (agent_id, kind or "UNKNOWN", key[:8], db.now().isoformat(), kind))


def touch(agent_id: str) -> None:
    """Record that the agent just used its key (shown as 'last used')."""
    db.run("UPDATE agent_connections SET last_used_at = ? WHERE agent_id = ?", (db.now().isoformat(), agent_id))
