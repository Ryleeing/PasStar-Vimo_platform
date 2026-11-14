"""外部审批（BE1）：买方在自己的系统里批准，agent 带着签名的授权凭证回来下单。

流程：
  1. POST /v1/orders 没带凭证，闸门结果是 BUYER_APPROVAL，协议的 approval_channel 是 EXTERNAL
     → 订单状态 BUYER_AUTHORIZATION_REQUIRED，附 build_request() 生成的 AuthorizationRequest。
  2. 买方系统（ERP、审批流）里由审批人批准，用审批人的 Ed25519 私钥签 BuyerAuthorizationClaims。
  3. agent 带着凭证调 POST /v1/orders/{order_id}/authorization（同一张订单继续走，不新建订单）。
     如果 agent 事先已经拿到凭证，也可以直接放在 POST /v1/orders 的 buyer_authorization 里。
  4. verify() 通过 → 视为买方已批准，闸门里 BUYER_APPROVAL 这一级放行；
     VALIO_REVIEW 和 REJECT 不受凭证影响。价格或库存变了照样 REQUOTE_REQUIRED，新报价要新凭证。

凭证只核验三件事：签名是不是协议里登记的审批人公钥、内容和这张报价对不对得上、有没有过期。
“提需求的人不能批自己的单”在外部审批下由买方系统负责；我们只拒绝 agent 自己的密钥签的凭证。
"""
from __future__ import annotations

import json
import time
from typing import Optional

from joserfc import jws
from joserfc.errors import JoseError
from joserfc.jwk import OKPKey

from ..schemas import AuthorizationRequest, BuyerAuthorizationClaims, Outcome, ProtocolBody, Quote

ALG = "Ed25519"                      # RFC 9864 的写法（旧写法 EdDSA 已弃用）
MAX_LIFETIME_S = 15 * 60
CLOCK_SKEW_S = 30


class AuthorizationError(Exception):
    """reason 取值：NOT_EXTERNAL_CHANNEL、NO_APPROVER_KEY、BAD_SIGNATURE、BAD_CLAIMS、PROTOCOL_MISMATCH、
    BUYER_MISMATCH、QUOTE_MISMATCH、AMOUNT_MISMATCH、APPROVER_MISMATCH、EXPIRED、LIFETIME_TOO_LONG、SIGNED_BY_AGENT_KEY"""

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
    """买方系统一侧用的（演示脚本和测试）。The Pass 自己从不调用。"""
    key = OKPKey.import_key(private_jwk)
    payload = json.dumps(claims.model_dump(), separators=(",", ":")).encode()
    return jws.serialize_compact({"alg": ALG, "kid": private_jwk.get("kid"), "typ": claims.typ}, payload,
                                 key, algorithms=[ALG])


def verify(token: str, quote: Quote, protocol_id: str, body: ProtocolBody,
           agent_public_jwk: Optional[dict] = None, now: Optional[float] = None) -> BuyerAuthorizationClaims:
    """通过则返回凭证内容，否则抛 AuthorizationError。"""
    now = time.time() if now is None else now
    if body.approval_channel != "EXTERNAL":
        raise AuthorizationError("NOT_EXTERNAL_CHANNEL", "This protocol uses hosted approval in The Pass")
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
    """把闸门结果换成订单状态（复核报价之后调用）。凭证只能放行 BUYER_APPROVAL 这一级。"""
    if outcome == "AUTO":
        return "PLACED"
    if outcome == "BUYER_APPROVAL":
        if channel == "EXTERNAL":
            return "PLACED" if authorized else "BUYER_AUTHORIZATION_REQUIRED"
        return "PENDING_BUYER_APPROVAL"
    if outcome == "VALIO_REVIEW":
        return "PENDING_VALIO_REVIEW"
    return "REJECTED"
