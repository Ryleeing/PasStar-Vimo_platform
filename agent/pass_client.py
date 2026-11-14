"""Thin client for The Pass public API, shared by the agent scripts.

Only the public HTTP API is used: these scripts stand in for an external buyer agent,
so they must never import backend code. The one exception is the buyer's own approval
system (backend/scripts/buyer_erp.py), which is run as a separate process because in
production it lives on the buyer's side.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx

REPO = pathlib.Path(__file__).resolve().parents[1]
BUYER_ERP = REPO / "backend" / "scripts" / "buyer_erp.py"
PENDING = {"PENDING_BUYER_APPROVAL", "PENDING_VALIO_REVIEW"}


class ApiError(Exception):
    """A non-2xx response in the shared error format."""

    def __init__(self, status: int, code: str, message: str, details: dict) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message, self.details = status, code, message, details


@dataclass
class Step:
    what: str
    detail: str = ""


@dataclass
class Outcome:
    """What happened in one purchase run, in order. `order` is the last order seen."""
    steps: list[Step] = field(default_factory=list)
    order: Optional[dict] = None
    error: Optional[ApiError] = None

    def log(self, what: str, detail: str = "") -> None:
        self.steps.append(Step(what, detail))

    @property
    def status(self) -> str:
        if self.error:
            return f"ERROR {self.error.code}"
        return self.order["status"] if self.order else "NO_ORDER"


class PassClient:
    def __init__(self, http: Optional[httpx.Client] = None, base_url: Optional[str] = None,
                 agent_key: Optional[str] = None, demo_user: Optional[str] = None) -> None:
        self.http = http or httpx.Client(base_url=base_url or os.environ.get("THE_PASS_URL", "http://localhost:8000"),
                                         timeout=30)
        self.headers: dict[str, str] = {}
        key = agent_key if agent_key is not None else os.environ.get("THE_PASS_AGENT_KEY", "demo")
        if key:
            self.headers["X-Agent-Key"] = key
        if demo_user:
            self.headers["X-Demo-User"] = demo_user

    def call(self, method: str, path: str, body: Any = None, headers: Optional[dict] = None) -> Any:
        r = self.http.request(method, path, json=body, headers={**self.headers, **(headers or {})})
        if r.status_code >= 400:
            try:
                err = r.json()["error"]
            except (ValueError, KeyError, TypeError):   # framework errors, e.g. a route that does not exist
                code = {404: "NOT_FOUND", 422: "VALIDATION_ERROR"}.get(r.status_code, "HTTP_ERROR")
                err = {"code": code, "message": r.text[:200], "details": {}}
            raise ApiError(r.status_code, err["code"], err["message"], err.get("details", {}))
        return r.json()

    # ───── endpoints ─────

    def parse(self, text: str, profile_id: Optional[str] = None) -> dict:
        return self.call("POST", "/v1/requests/parse", {"text": text, "profile_id": profile_id})

    def create_request(self, protocol_id: str, structured: dict, profile_id: Optional[str] = None,
                       raw_text: Optional[str] = None) -> dict:
        return self.call("POST", "/v1/requests", {
            "protocol_id": protocol_id, "profile_id": profile_id, "structured": structured, "raw_text": raw_text,
            "input_mode": "NATURAL_LANGUAGE" if raw_text else "PANEL"})

    def select(self, request_id: str, candidate_id: str) -> dict:
        return self.call("POST", f"/v1/requests/{request_id}/select", {"candidate_id": candidate_id})

    def place_order(self, quote_id: str, protocol_id: str, idempotency_key: Optional[str] = None,
                    buyer_authorization: Optional[str] = None) -> dict:
        body = {"quote_id": quote_id, "protocol_id": protocol_id}
        if buyer_authorization:
            body["buyer_authorization"] = buyer_authorization
        return self.call("POST", "/v1/orders", body, {"Idempotency-Key": idempotency_key or f"agent-{uuid.uuid4()}"})

    def get_order(self, order_id: str) -> dict:
        return self.call("GET", f"/v1/orders/{order_id}")

    def authorize(self, order_id: str, token: str) -> dict:
        return self.call("POST", f"/v1/orders/{order_id}/authorization", {"buyer_authorization": token})


def buyer_erp_approve(order: dict) -> str:
    """Ask the buyer's own system to approve and sign. Returns the compact JWS."""
    res = subprocess.run([sys.executable, str(BUYER_ERP), "approve", "-", "--yes"], input=json.dumps(order),
                         capture_output=True, text=True, check=True)
    return res.stdout.strip()


def finish_order(client: PassClient, quote_id: str, protocol_id: str, out: Outcome,
                 approve: Callable[[dict], str] = buyer_erp_approve,
                 poll_every_s: float = 2.0, max_polls: int = 5, max_requotes: int = 1) -> Outcome:
    """Place an order and follow it to a final state: PLACED, REJECTED, still pending, or an error.

    Handles every order status in the contract: polls PENDING_*, gets a signed authorization for
    BUYER_AUTHORIZATION_REQUIRED, and retries once on REQUOTE_REQUIRED with the new quote."""
    key = f"agent-{uuid.uuid4()}"   # one key per quote: a retry of the same call returns the same order
    try:
        order = client.place_order(quote_id, protocol_id, key)
        out.log("Placed order", f"{order['id']} -> {order['status']}")

        for _ in range(max_requotes + 1):
            if order["status"] == "BUYER_AUTHORIZATION_REQUIRED":
                req = order["authorization_request"]
                out.log("Needs buyer authorization", f"€{req['amount_gross_cents'] / 100:,.2f}, approver {req['approver']}")
                token = approve(order)
                out.log("Buyer system signed an authorization", f"{len(token)}-char JWS")
                order = client.authorize(order["id"], token)
                out.log("Sent authorization", f"{order['id']} -> {order['status']}")

            polls = 0
            while order["status"] in PENDING and polls < max_polls:
                polls += 1
                time.sleep(poll_every_s)
                order = client.get_order(order["id"])
                out.log("Polled order", f"{order['id']} -> {order['status']}")

            if order["status"] == "REQUOTE_REQUIRED" and order.get("new_quote_id"):
                out.log("Price or stock changed", f"retrying with new quote {order['new_quote_id']}")
                key = f"agent-{uuid.uuid4()}"
                order = client.place_order(order["new_quote_id"], protocol_id, key)
                out.log("Placed order", f"{order['id']} -> {order['status']}")
                continue
            break
        out.order = order
    except ApiError as e:
        out.error = e
        out.log("API error", str(e))
    return out


def print_outcome(out: Outcome) -> None:
    for i, s in enumerate(out.steps, 1):
        print(f"{i:>2}. {s.what}" + (f": {s.detail}" if s.detail else ""))
    print(f"Result: {out.status}")
