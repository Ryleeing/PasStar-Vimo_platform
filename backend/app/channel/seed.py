"""Demo seed data (BE1 tables). Written at startup when the database is empty; POST /v1/admin/reset rewrites it.
Auto-approve limit €1,000, order maximum €3,000: 1,000 litres of milk costs roughly a thousand euros at normal
prices. Tune both once real prices are known."""
from __future__ import annotations

import os
import secrets

from datetime import timedelta

from .. import db, demo_keys
from ..schemas import ApproverKey, Protocol, ProtocolBody, Requirement
from . import audit, checkpoints, protocols
from .identity import key_hash

TABLES = ["audit_log", "approvals", "orders", "quotes", "protocols", "agent_connections", "agents", "users", "buyers"]

BUYERS = [("buy_hotel", "Hotel Helsinki Central", "Hotel Helsinki Central, loading dock"),
          ("buy_hospital", "Helsinki Hospital Kitchen", "Hospital kitchen, gate B"),
          ("buy_bistro", "Bistro Kallio", "Bistro Kallio, back door")]             # small restaurant in SELF mode
USERS = [("usr_chef", "BUYER", "buy_hotel", "Chef", "REQUESTER"),
         ("usr_manager", "BUYER", "buy_hotel", "Procurement manager", "APPROVER"),
         ("usr_hospital_chef", "BUYER", "buy_hospital", "Ward kitchen chef", "REQUESTER"),
         ("usr_hospital_manager", "BUYER", "buy_hospital", "Hospital procurement manager", "APPROVER"),
         ("usr_owner", "BUYER", "buy_bistro", "Owner", "OWNER"),
         ("usr_valio", "VALIO", None, "Valio channel owner", "VALIO_REVIEWER")]
AGENTS = [("agt_hotel", "buy_hotel", "Hotel purchasing agent", "demo-hotel"),      # last item: the demo X-Agent-Key
          ("agt_hotel_ext", "buy_hotel", "Hotel ERP purchasing agent", "demo"),
          ("agt_hospital", "buy_hospital", "Hospital purchasing agent", "demo-hospital"),
          ("agt_bistro", "buy_bistro", "Bistro purchasing agent", "demo-bistro"),
          ("agt_rogue", "buy_hotel", "Unknown Procurement Bot", "demo-rogue")]     # the misbehaving agent; has no protocol
# How each demo agent connects: the hotel's ChatGPT plugin and ERP, the hospital's and bistro's assistants.
AGENT_KINDS = {"agt_hotel": "CHATGPT", "agt_hotel_ext": "ERP", "agt_hospital": "CHATGPT", "agt_bistro": "CLAUDE",
               "agt_rogue": "UNKNOWN"}

LACTOSE_FREE = Requirement(kind="CLAIM", code="FREE_FROM_LACTOSE", label="Lactose-free")
NUT_FREE = Requirement(kind="ALLERGEN_FREE", code="AN", label="Nut-free")


def _body(buyer: str, agent: str, approver: str, **over) -> ProtocolBody:
    return ProtocolBody(buyer_id=buyer, agent_id=agent, auto_approve_limit_cents=100_000, max_order_cents=300_000,
                        allowed_categories=["10400", "10401", "10405"], approver=approver,
                        valid_until=db.now().date() + timedelta(days=91), **over)


# Starts unsigned, so the demo can show step 0: the owner signs the protocol, Valio confirms, then ordering starts.
ONBOARDING = {"prot_bistro_v1"}


def reset(onboarding: bool = True) -> None:
    """onboarding=True leaves the ONBOARDING protocols out (the app and /v1/admin/reset); tests sign them all."""
    for t in TABLES:
        db.run(f"DELETE FROM {t}")
    db.run("DELETE FROM sqlite_sequence WHERE name = 'audit_log'")
    checkpoints._calls.clear()
    checkpoints._pending.clear()
    checkpoints._written.clear()

    for b in BUYERS:
        db.run("INSERT INTO buyers (id, name, delivery_address) VALUES (?,?,?)", b)
    for u in USERS:
        db.run("INSERT INTO users (id, org, buyer_id, name, role) VALUES (?,?,?,?,?)", u)
    public = os.environ.get("PASSTAR_PUBLIC") == "1"
    for agent_id, buyer_id, name, api_key in AGENTS:
        if public:                  # on a public deployment the demo keys in this file must not work: random ones
            api_key = f"tp_{secrets.token_urlsafe(24)}"   # nobody knows; issue real keys on the Agents page
        db.run("INSERT INTO agents (id, buyer_id, name, api_key_hash) VALUES (?,?,?,?)",
               (agent_id, buyer_id, name, key_hash(api_key)))
        db.run("INSERT INTO agent_connections (agent_id, kind, key_hint, key_issued_at) VALUES (?,?,?,?)",
               (agent_id, AGENT_KINDS[agent_id], api_key[:8], db.now().isoformat()))

    bodies = {
        "prot_hotel_v1": _body("buy_hotel", "agt_hotel", "usr_manager", hard_requirements=[LACTOSE_FREE]),
        # external approval: over-limit orders are approved in the hotel ERP
        "prot_hotel_ext_v1": _body("buy_hotel", "agt_hotel_ext", "usr_manager", hard_requirements=[LACTOSE_FREE],
                                   approval_channel="EXTERNAL", approver_public_key=ApproverKey(**demo_keys.public_jwk())),
        "prot_hospital_v1": _body("buy_hospital", "agt_hospital", "usr_hospital_manager", hard_requirements=[NUT_FREE]),
        # SELF mode: the owner both requests and approves
        "prot_bistro_v1": _body("buy_bistro", "agt_bistro", "usr_owner", approval_mode="SELF"),
    }
    if onboarding:
        bodies = {k: v for k, v in bodies.items() if k not in ONBOARDING}
    now = db.now()
    for protocol_id, body in bodies.items():
        protocols.insert(Protocol(id=protocol_id, version=1, status="ACTIVE", body=body,
                                  jws=protocols.jws_for(protocol_id, 1, body), created_at=now,
                                  buyer_signed_at=now, valio_confirmed_at=now))
    audit.append("SEED_LOADED", "system", {"protocols": list(bodies)}, visibility="VALIO", summary="Demo seed data loaded")
