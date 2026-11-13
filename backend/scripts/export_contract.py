"""导出 openapi.json 和 fixtures/*.json（给前端用）。在 backend/ 目录下运行：python scripts/export_contract.py"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

root = pathlib.Path(__file__).resolve().parents[2]
(root / "openapi.json").write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8")

c = TestClient(app)
H = {"X-Demo-User": "usr_chef", "X-Agent-Key": "demo"}
calls = {
    "protocol": ("get", "/v1/protocols/prot_hotel_v1", None),
    "protocol_diff": ("post", "/v1/protocols/prot_hotel_v1/amend", {"changes": {}}),
    "profiles": ("get", "/v1/buyers/buy_hotel/profiles", None),
    "facets": ("get", "/v1/catalog/facets", None),
    "products_search": ("post", "/v1/products/search", {"requirements": []}),
    "parse_result": ("post", "/v1/requests/parse", {"text": "1000 litres of lactose-free milk by Monday"}),
    "request_result": ("get", "/v1/requests/req_1", None),
    "chain_timeline": ("get", "/v1/requests/req_1/chain", None),
    "select_result": ("post", "/v1/requests/req_1/select", {"candidate_id": "cand_1"}),
    "order": ("get", "/v1/orders/ord_1", None),
    "approvals": ("get", "/v1/approvals", None),
    "audit": ("get", "/v1/audit", None),
    "faults": ("get", "/v1/admin/faults", None),
}
out = root / "fixtures"
out.mkdir(exist_ok=True)
for name, (method, path, body) in calls.items():
    r = getattr(c, method)(path, headers=H, **({"json": body} if body is not None else {}))
    assert r.status_code == 200, (name, r.status_code, r.text)
    (out / f"{name}.json").write_text(json.dumps(r.json(), ensure_ascii=False, indent=2), encoding="utf-8")
# 幂等性自检
o1 = c.post("/v1/orders", json={"quote_id": "quo_1", "protocol_id": "prot_hotel_v1"}, headers={**H, "Idempotency-Key": "k1"}).json()
o2 = c.post("/v1/orders", json={"quote_id": "quo_1", "protocol_id": "prot_hotel_v1"}, headers={**H, "Idempotency-Key": "k1"}).json()
assert o1 == o2
print("openapi paths:", len(app.openapi()["paths"]), "| fixtures:", len(calls))
