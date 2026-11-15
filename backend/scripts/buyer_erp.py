"""Demo only: simulates "the hotel's own approval system". The approver approves here, and the system signs the authorisation token with the approver's private key.

Usage (from the backend/ directory):
  python scripts/buyer_erp.py public-key                 # print the public key, to register in the external-approval protocol
  python scripts/buyer_erp.py approve request.json       # read an AuthorizationRequest, print the token once the approver confirms
  python scripts/buyer_erp.py approve - --yes < req.json # read from stdin, skip confirmation (for scripts and the demo console)

In production this step happens in the buyer's ERP or approval workflow, and PasStar never gets this private key.
"""
import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app import demo_keys  # noqa: E402
from app.channel.authorization import MAX_LIFETIME_S, sign  # noqa: E402
from app.schemas import AuthorizationRequest, BuyerAuthorizationClaims  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("public-key")
    a = sub.add_parser("approve")
    a.add_argument("request", help="AuthorizationRequest JSON file, or - for stdin")
    a.add_argument("--yes", action="store_true", help="approve without asking")
    args = ap.parse_args()

    if args.cmd == "public-key":
        print(json.dumps(demo_keys.public_jwk(), indent=2))
        return

    raw = sys.stdin.read() if args.request == "-" else pathlib.Path(args.request).read_text()
    data = json.loads(raw)
    req = AuthorizationRequest.model_validate(data.get("authorization_request", data))  # a whole Order is accepted too
    print(f"[Hotel ERP] {req.approver} is asked to approve quote {req.quote_id}: "
          f"€{req.amount_gross_cents / 100:,.2f}, delivery {req.delivery_date}", file=sys.stderr)
    for r in req.reasons:
        print(f"  reason: {r}", file=sys.stderr)
    if not args.yes and input("Approve? [y/N] ").strip().lower() != "y":
        print("[Hotel ERP] Not approved; no authorization issued.", file=sys.stderr)
        sys.exit(1)

    now = int(time.time())
    claims = BuyerAuthorizationClaims(protocol_id=req.protocol_id, buyer_id=req.buyer_id, quote_id=req.quote_id,
                                      amount_gross_cents=req.amount_gross_cents, approver=req.approver,
                                      iat=now, exp=now + MAX_LIFETIME_S)
    print(sign(claims, demo_keys.load_or_create_private()))


if __name__ == "__main__":
    main()
