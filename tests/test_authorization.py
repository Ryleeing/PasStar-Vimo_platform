"""External approval: the authorization token signed by the buyer's system. HTTP flow tests are in test_channel.py."""
import time

import pytest
from joserfc.jwk import OKPKey

from app import demo_keys, fixtures as fx
from app.channel.authorization import AuthorizationError, resolve_buyer_gate, sign, verify
from app.channel.protocol_rules import classify_changes
from app.schemas import BuyerAuthorizationClaims, ProtocolBody

BODY = fx.PROTOCOL_EXTERNAL.body
PID = fx.PROTOCOL_EXTERNAL.id
Q = fx.QUOTE_EXT
NOW = time.time()


def claims(**over):
    base = dict(protocol_id=PID, buyer_id="buy_hotel", quote_id=Q.id, amount_gross_cents=Q.totals.gross_cents,
                approver="usr_manager", iat=int(NOW), exp=int(NOW) + 600)
    return BuyerAuthorizationClaims(**{**base, **over})


def token(**over):
    return sign(claims(**over), demo_keys.load_or_create_private())


def reason(tok, **kw):
    with pytest.raises(AuthorizationError) as e:
        verify(tok, Q, PID, BODY, now=NOW, **kw)
    return e.value.reason


def test_valid_authorization():
    assert verify(token(), Q, PID, BODY, now=NOW).approver == "usr_manager"


def test_signed_by_other_key_is_rejected():
    other = {**OKPKey.generate_key("Ed25519").as_dict(private=True), "kid": "x"}
    assert reason(sign(claims(), other)) == "BAD_SIGNATURE"


@pytest.mark.parametrize("over,expected", [
    ({"quote_id": "quo_other"}, "QUOTE_MISMATCH"),
    ({"amount_gross_cents": 1}, "AMOUNT_MISMATCH"),
    ({"protocol_id": "prot_other"}, "PROTOCOL_MISMATCH"),
    ({"approver": "usr_chef"}, "APPROVER_MISMATCH"),
    ({"iat": int(NOW) - 3600, "exp": int(NOW) - 3000}, "EXPIRED"),
    ({"exp": int(NOW) + 3600}, "LIFETIME_TOO_LONG"),
])
def test_claims_must_match_the_quote(over, expected):
    assert reason(token(**over)) == expected


def test_agent_key_cannot_be_the_approver_key():
    assert reason(token(), agent_public_jwk=demo_keys.public_jwk()) == "SIGNED_BY_AGENT_KEY"


def test_hosted_protocol_does_not_accept_authorizations():
    with pytest.raises(AuthorizationError) as e:
        verify(token(), Q, PID, fx.PROTOCOL.body, now=NOW)
    assert e.value.reason == "NOT_EXTERNAL_CHANNEL"


def test_authorization_only_lifts_buyer_approval():
    assert resolve_buyer_gate("BUYER_APPROVAL", "EXTERNAL", False) == "BUYER_AUTHORIZATION_REQUIRED"
    assert resolve_buyer_gate("BUYER_APPROVAL", "EXTERNAL", True) == "PLACED"
    assert resolve_buyer_gate("BUYER_APPROVAL", "HOSTED", False) == "PENDING_BUYER_APPROVAL"
    assert resolve_buyer_gate("VALIO_REVIEW", "EXTERNAL", True) == "PENDING_VALIO_REVIEW"
    assert resolve_buyer_gate("REJECT", "EXTERNAL", True) == "REJECTED"


def test_external_protocol_requires_key():
    with pytest.raises(ValueError):
        ProtocolBody.model_validate({**fx.PROTOCOL.body.model_dump(mode="json"), "approval_channel": "EXTERNAL"})


def test_changing_channel_or_key_needs_new_signature():
    r = classify_changes(fx.PROTOCOL.body, {"approval_channel": "EXTERNAL"}, "buyer", fx.MONDAY)
    assert [c.field for c in r["expanded"]] == ["approval_channel"]

