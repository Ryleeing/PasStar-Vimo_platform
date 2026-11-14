"""演示用：模拟“酒店自己的审批系统”的密钥。

私钥只属于买方系统，正式环境里 The Pass 永远拿不到它。演示时为了一台电脑跑完全程，
第一次用到时在 data/demo/ 下生成（data/ 不进仓库），scripts/buyer_erp.py 用它签凭证，
假服务器只读取其中的公钥，写进外部审批协议的 approver_public_key。
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
