"""Export openapi.json and fixtures/*.json for the frontend. Run from backend/: python scripts/export_contract.py

BE2 routes still serve mock data. BE3 routes are real: their examples come from one real run on a throwaway
database with the demo seed (rule-based parser, no LLM call). BE1 examples are dumped from app/fixtures.py."""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient  # noqa: E402

from app import db, fixtures as fx  # noqa: E402
from app.channel import reports, seed  # noqa: E402
from app.main import app  # noqa: E402
from app.orchestrator import parser_api, seed as be3_seed  # noqa: E402
from app.orchestrator.llm import LlmClient  # noqa: E402

db.DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "export.db"
db.init()
seed.reset()
be3_seed.reset()
parser_api.llm = LlmClient(task="parse", api_key="")          # examples must not depend on an LLM key

root = pathlib.Path(__file__).resolve().parents[2]
(root / "openapi.json").write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
out = root / "fixtures"
out.mkdir(exist_ok=True)


def write(name: str, data) -> None:
    (out / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


c = TestClient(app)
CHEF = {"X-Demo-User": "usr_chef"}


def call(name: str, method: str, path: str, body=None, headers=None):
    r = getattr(c, method)(path, headers=headers or {}, **({"json": body} if body is not None else {}))
    assert r.status_code == 200, (name, r.status_code, r.text)
    write(name, r.json())
    return r.json()


call("profiles", "get", "/v1/buyers/buy_hotel/profiles", headers=CHEF)
call("facets", "get", "/v1/catalog/facets")
call("products_search", "post", "/v1/products/search", {"requirements": []})
call("parse_result", "post", "/v1/requests/parse", {"text": "1000 litres of lactose-free milk by Monday"}, CHEF)
result = call("request_result", "post", "/v1/requests", {
    "protocol_id": "prot_hotel_v1", "profile_id": "prof_banquet", "input_mode": "PANEL",
    "structured": fx.STRUCTURED.model_dump(mode="json")}, CHEF)
rid = result["request_id"]
call("chain_timeline", "get", f"/v1/requests/{rid}/chain", headers=CHEF)
call("select_result", "post", f"/v1/requests/{rid}/select", {"candidate_id": result["candidates"][0]["id"]}, CHEF)
call("faults", "get", "/v1/admin/faults")
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
print("openapi paths:", len(app.openapi()["paths"]), "| fixtures:", len(list(out.glob("*.json"))))
