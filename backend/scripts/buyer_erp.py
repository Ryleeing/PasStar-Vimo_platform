"""演示用：模拟“酒店自己的审批系统”。审批人在这里批准，系统用审批人的私钥签授权凭证。

用法（在 backend/ 目录下）：
  python scripts/buyer_erp.py public-key                 # 打印公钥，登记到外部审批协议里
  python scripts/buyer_erp.py approve request.json       # 读 AuthorizationRequest，审批人确认后打印凭证
  python scripts/buyer_erp.py approve - --yes < req.json # 从标准输入读，跳过确认（给脚本和演示台用）

正式环境里这一步发生在买方的 ERP 或审批流里，PasStar 永远拿不到这把私钥。
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
    a.add_argument("request", help="AuthorizationRequest 的 JSON 文件，或 - 表示标准输入")
    a.add_argument("--yes", action="store_true", help="不询问，直接批准")
    args = ap.parse_args()

    if args.cmd == "public-key":
        print(json.dumps(demo_keys.public_jwk(), indent=2))
        return

    raw = sys.stdin.read() if args.request == "-" else pathlib.Path(args.request).read_text()
    data = json.loads(raw)
    req = AuthorizationRequest.model_validate(data.get("authorization_request", data))  # 也接受整张 Order
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
