"""Export openapi.json and fixtures/*.json for the frontend. Run from backend/: python scripts/export_contract.py

BE2 / BE3 routes still serve mock data, so their fixtures are fetched over HTTP. BE1 routes are real and need a
database plus the BE2 / BE3 implementations, so the BE1 examples are dumped straight from app/fixtures.py."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient  # noqa: E402

from app import fixtures as fx  # noqa: E402
from app.channel import reports  # noqa: E402
from app.main import app  # noqa: E402

root = pathlib.Path(__file__).resolve().parents[2]
(root / "openapi.json").write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
out = root / "fixtures"
out.mkdir(exist_ok=True)


def write(name: str, data) -> None:
    (out / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


c = TestClient(app)
calls = {
    "profiles": ("get", "/v1/buyers/buy_hotel/profiles", None),
    "facets": ("get", "/v1/catalog/facets", None),
    "products_search": ("post", "/v1/products/search", {"requirements": []}),
    "parse_result": ("post", "/v1/requests/parse", {"text": "1000 litres of lactose-free milk by Monday"}),
    "request_result": ("get", "/v1/requests/req_1", None),
    "chain_timeline": ("get", "/v1/requests/req_1/chain", None),
    "select_result": ("post", "/v1/requests/req_1/select", {"candidate_id": "cand_1"}),
    "faults": ("get", "/v1/admin/faults", None),
}
for name, (method, path, body) in calls.items():
    r = getattr(c, method)(path, **({"json": body} if body is not None else {}))
    assert r.status_code == 200, (name, r.status_code, r.text)
    write(name, r.json())

examples = {
    "protocol": fx.PROTOCOL,
    "protocol_external": fx.PROTOCOL_EXTERNAL,
    "protocol_diff": fx.PROTOCOL_DIFF,
    "order": fx.ORDER,
    "order_auth_required": fx.ORDER_AUTH_REQUIRED,
    "approvals": [fx.APPROVAL_BUYER, fx.APPROVAL_VALIO],
    "audit": fx.AUDIT,
    "holds_buyer": reports.EXAMPLE_BUYER,
    "holds_valio": reports.EXAMPLE_VALIO,
}
for name, obj in examples.items():
    write(name, [o.model_dump(mode="json") for o in obj] if isinstance(obj, list) else obj.model_dump(mode="json"))
print("openapi paths:", len(app.openapi()["paths"]), "| fixtures:", len(calls) + len(examples))
