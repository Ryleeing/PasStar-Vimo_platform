"""Valio's Ed25519 signing key and JWS. Protocols and quotes are signed with it; the public key is served at
/v1/.well-known/jwks.json. The key is generated under data/keys/ on first use (not in the repo)."""
from __future__ import annotations

import json

from joserfc import jws
from joserfc.jwk import OKPKey

from .. import db
from ..schemas import Jwk, Jwks

ALG = "Ed25519"
KID = "valio-1"
KEY_FILE = db.ROOT / "data" / "keys" / "valio_signing_key.json"


def _private() -> dict:
    if KEY_FILE.exists():
        return json.loads(KEY_FILE.read_text())
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    jwk = {**OKPKey.generate_key("Ed25519").as_dict(private=True), "kid": KID}
    KEY_FILE.write_text(json.dumps(jwk))
    return jwk


def sign(payload: dict, typ: str) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return jws.serialize_compact({"alg": ALG, "kid": KID, "typ": typ}, body, OKPKey.import_key(_private()), algorithms=[ALG])


def verify(token: str) -> dict:
    """Verify the signature and return the signed payload; a bad signature raises joserfc's JoseError."""
    return json.loads(jws.deserialize_compact(token, OKPKey.import_key(_private()), algorithms=[ALG]).payload)


def jwks() -> Jwks:
    return Jwks(keys=[Jwk(kid=KID, x=_private()["x"])])
