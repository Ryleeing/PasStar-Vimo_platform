"""Who may act as a person on a public deployment.

Locally (the default) people are identified by the X-Demo-User header and only requests from this machine are
accepted (see main.py). On a public deployment (PASSTAR_PUBLIC=1) every visitor comes through the platform's proxy,
so instead a shared demo passcode (PASSTAR_DEMO_PASSCODE) is exchanged once for a session token, which the screens
send as X-Demo-Session with every request. Agents are not affected: they always use their own X-Agent-Key.

The token is an HMAC of the passcode under PASSTAR_SESSION_SECRET (random per process when unset, so a restart
signs everyone out). The passcode itself is never stored or logged.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from typing import Optional

_SECRET = os.environ.get("PASSTAR_SESSION_SECRET") or secrets.token_hex(32)


def public() -> bool:
    return os.environ.get("PASSTAR_PUBLIC") == "1"


def _passcode() -> Optional[str]:
    return os.environ.get("PASSTAR_DEMO_PASSCODE") or None


def _token() -> Optional[str]:
    code = _passcode()
    return hmac.new(_SECRET.encode(), code.encode(), hashlib.sha256).hexdigest() if code else None


def sign_in(passcode: str) -> Optional[str]:
    """The session token for the right passcode, else None."""
    code = _passcode()
    if not code or not hmac.compare_digest(passcode.encode(), code.encode()):
        return None
    return _token()


def valid(token: Optional[str]) -> bool:
    expected = _token()
    return bool(token and expected and hmac.compare_digest(token, expected))
