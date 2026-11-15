"""External approval (BE1): the buyer approves in their own system, and the agent comes back with a signed authorisation token to place the order.

Flow:
  1. POST /v1/orders without a token, the gate result is BUYER_APPROVAL, and the protocol's approval_channel is EXTERNAL
     → order status BUYER_AUTHORIZATION_REQUIRED, with the AuthorizationRequest built by build_request().
  2. The approver approves in the buyer's system (ERP, approval workflow), signing BuyerAuthorizationClaims with the approver's Ed25519 private key.
  3. The agent calls POST /v1/orders/{order_id}/authorization with the token (the same order continues; no new order is created).
     If the agent already has a token, it can also put it straight into buyer_authorization on POST /v1/orders.
  4. verify() passes → treated as approved by the buyer, and the gate releases the BUYER_APPROVAL level;
     VALIO_REVIEW and REJECT are not affected by the token. A price or stock change still gives REQUOTE_REQUIRED, and a new quote needs a new token.

The token is checked for three things only: that the signature matches the approver public key registered in the protocol, that the content matches this quote, and that it has not expired.
"Whoever made the request cannot approve it" is the buyer system's job under external approval; we only reject tokens signed with the agent's own key.
"""
from __future__ import annotations

import json
import time
from typing import Optional

from joserfc import jws
from joserfc.errors import JoseError
from joserfc.jwk import OKPKey

from ..schemas import AuthorizationRequest, BuyerAuthorizationClaims, Outcome, ProtocolBody, Quote

ALG = "Ed25519"                      # RFC 9864 spelling (the old "EdDSA" is deprecated)
MAX_LIFETIME_S = 15 * 60
CLOCK_SKEW_S = 30


class AuthorizationError(Exception):
    """reason values: NOT_EXTERNAL_CHANNEL, NO_APPROVER_KEY, BAD_SIGNATURE, BAD_CLAIMS, PROTOCOL_MISMATCH,
    BUYER_MISMATCH, QUOTE_MISMATCH, AMOUNT_MISMATCH, APPROVER_MISMATCH, EXPIRED, LIFETIME_TOO_LONG, SIGNED_BY_AGENT_KEY"""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


def build_request(quote: Quote, protocol_id: str, body: ProtocolBody,
                  reason_codes: list[str], reasons: list[str]) -> AuthorizationRequest:
    if body.approver_public_key is None:
        raise AuthorizationError("NO_APPROVER_KEY", "Protocol has no approver public key")
    return AuthorizationRequest(
        protocol_id=protocol_id, buyer_id=body.buyer_id, request_id=quote.request_id, quote_id=quote.id,
        amount_gross_cents=quote.totals.gross_cents, delivery_date=quote.delivery_date,
        quote_valid_until=quote.valid_until, reason_codes=reason_codes, reasons=reasons,
        approver=body.approver, sign_with_kid=body.approver_public_key.kid,
    )


def sign(claims: BuyerAuthorizationClaims, private_jwk: dict) -> str:
    """Used on the buyer-system side (demo scripts and tests). PasStar itself never calls it."""
    key = OKPKey.import_key(private_jwk)
    payload = json.dumps(claims.model_dump(), separators=(",", ":")).encode()
    return jws.serialize_compact({"alg": ALG, "kid": private_jwk.get("kid"), "typ": claims.typ}, payload,
                                 key, algorithms=[ALG])


def verify(token: str, quote: Quote, protocol_id: str, body: ProtocolBody,
           agent_public_jwk: Optional[dict] = None, now: Optional[float] = None) -> BuyerAuthorizationClaims:
    """Returns the token contents if valid, otherwise raises AuthorizationError."""
    now = time.time() if now is None else now
    if body.approval_channel != "EXTERNAL":
        raise AuthorizationError("NOT_EXTERNAL_CHANNEL", "This protocol uses hosted approval in PasStar")
    if body.approver_public_key is None:
        raise AuthorizationError("NO_APPROVER_KEY", "Protocol has no approver public key")
    approver_jwk = body.approver_public_key.model_dump()
    if agent_public_jwk and agent_public_jwk.get("x") == approver_jwk["x"]:
        raise AuthorizationError("SIGNED_BY_AGENT_KEY", "The approver key is the agent's own key")

    try:
        obj = jws.deserialize_compact(token, OKPKey.import_key(approver_jwk), algorithms=[ALG])
    except (JoseError, ValueError) as e:
        raise AuthorizationError("BAD_SIGNATURE", f"Signature does not verify with the approver key: {e}") from e
    try:
        c = BuyerAuthorizationClaims.model_validate_json(obj.payload)
    except ValueError as e:
        raise AuthorizationError("BAD_CLAIMS", "Authorization payload is not a buyer-authorization/v1") from e

    checks = [
        (c.protocol_id == protocol_id, "PROTOCOL_MISMATCH", "Authorization is for another protocol"),
        (c.buyer_id == body.buyer_id, "BUYER_MISMATCH", "Authorization is for another buyer"),
        (c.quote_id == quote.id, "QUOTE_MISMATCH", "Authorization is for another quote"),
        (c.amount_gross_cents == quote.totals.gross_cents, "AMOUNT_MISMATCH", "Authorized amount differs from the quote"),
        (c.approver == body.approver, "APPROVER_MISMATCH", "Signed by someone other than the protocol approver"),
        (c.exp - c.iat <= MAX_LIFETIME_S, "LIFETIME_TOO_LONG", "Authorization may be valid for at most 15 minutes"),
        (c.iat <= now + CLOCK_SKEW_S and now <= c.exp + CLOCK_SKEW_S, "EXPIRED", "Authorization is expired or not yet valid"),
    ]
    for ok, reason, msg in checks:
        if not ok:
            raise AuthorizationError(reason, msg)
    return c


def resolve_buyer_gate(outcome: Outcome, channel: str, authorized: bool) -> str:
    """Turn the gate result into an order status (called after re-checking the quote). A token can only release the BUYER_APPROVAL level."""
    if outcome == "AUTO":
        return "PLACED"
    if outcome == "BUYER_APPROVAL":
        if channel == "EXTERNAL":
            return "PLACED" if authorized else "BUYER_AUTHORIZATION_REQUIRED"
        return "PENDING_BUYER_APPROVAL"
    if outcome == "VALIO_REVIEW":
        return "PENDING_VALIO_REVIEW"
    return "REJECTED"
