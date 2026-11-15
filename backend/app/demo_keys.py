"""Demo only: keys simulating "the hotel's own approval system".

The private key belongs only to the buyer's system; in production PasStar never gets it. So the whole demo can run on one computer,
it is generated under data/demo/ on first use (data/ is not in the repo); scripts/buyer_erp.py signs tokens with it,
and the fake server only reads its public key, writing it into the external-approval protocol's approver_public_key.
"""
from __future__ import annotations

import json
import pathlib

from joserfc.jwk import OKPKey

ROOT = pathlib.Path(__file__).resolve().parents[2]
KEY_FILE = ROOT / "data" / "demo" / "buyer_erp_key.json"
KID = "hotel-erp-1"


def load_or_create_private() -> dict:
    if KEY_FILE.exists():
        return json.loads(KEY_FILE.read_text())
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    jwk = {**OKPKey.generate_key("Ed25519").as_dict(private=True), "kid": KID}
    KEY_FILE.write_text(json.dumps(jwk))
    return jwk


def public_jwk() -> dict:
    d = load_or_create_private()
    return {"kty": "OKP", "crv": "Ed25519", "kid": d.get("kid", KID), "x": d["x"]}
