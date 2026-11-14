# The Pass · Yes We Cooked

Valio Aimo open procurement channel for AI purchasing agents.

## 快速开始（M1 假数据服务器）

```
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
# 打开 http://localhost:8000/docs
```

前端生成类型：`npx openapi-typescript ../openapi.json -o src/api/types.ts`
假数据：`fixtures/*.json`（运行 `python scripts/export_contract.py` 可重新导出）

## 演示用户和审批模式

界面请求带 `X-Demo-User` 头（演示用，不做登录）：

| 模式（协议里的 `approval_mode`） | 用户 | 能做什么 |
|---|---|---|
| SEPARATE 组织模式（主展示） | `usr_chef` | 提需求、选方案；不能批自己的单（403 `SELF_APPROVAL_FORBIDDEN`） |
| | `usr_manager` | 审批超出协议的单、签协议 |
| SELF 单人模式 | `usr_owner` | 提需求，也能批自己的单（日志记为 self-approved） |
| 两种模式都有 | `usr_valio` | Valio 审批台、agent 管理、完整审计 |

审批人校验在 `backend/app/channel/approvals.py`。

## Agent 客户与外部审批

外部采购 agent 直接调接口：`POST /v1/requests`（结构化需求）→ `POST /v1/requests/{id}/select`（签名报价）→ `POST /v1/orders`。

协议的 `approval_channel` 决定超额时怎么批：
- `HOSTED`：在 The Pass 的审批页上批，订单进 `PENDING_BUYER_APPROVAL`，agent 轮询结果；
- `EXTERNAL`：在买方自己的系统里批。订单返回 `BUYER_AUTHORIZATION_REQUIRED` 和 `authorization_request`；买方系统用审批人的私钥签一张只对这张报价有效的凭证；agent 调 `POST /v1/orders/{id}/authorization` 带回凭证，验过就下单。

本地演示（假服务器里 `prot_hotel_ext_v1` 走外部审批）：

```bash
cd backend
# 1. agent 下单，拿到 BUYER_AUTHORIZATION_REQUIRED
curl -s -X POST localhost:8000/v1/orders -H 'Content-Type: application/json' -H 'X-Agent-Key: demo' \
  -H 'Idempotency-Key: demo-1' -d '{"quote_id":"quo_7","protocol_id":"prot_hotel_ext_v1"}' > order.json
# 2. “酒店系统”里经理批准，签出凭证
TOKEN=$(python scripts/buyer_erp.py approve order.json --yes)
# 3. agent 带凭证回来
curl -s -X POST localhost:8000/v1/orders/ord_7/authorization -H 'Content-Type: application/json' \
  -H 'X-Agent-Key: demo' -d "{\"buyer_authorization\":\"$TOKEN\"}"
```

演示用的酒店系统密钥第一次用到时生成在 `data/demo/`（不进仓库）。凭证校验在 `backend/app/channel/authorization.py`。

## 目录

- `backend/app/schemas.py`  接口契约（所有 HTTP 请求和响应）
- `backend/app/internal.py` 后端模块之间的内部接口
- `backend/app/schema.sql`  数据库建表
- `backend/app/main.py`     路由（M1 返回假数据，逐个替换成真实实现）
- `backend/app/channel/`    BE1 通道与交易
- `backend/app/catalog/`    BE2 目录与数据
- `backend/app/orchestrator/` BE3 编排与 AI
- `backend/app/config/`     rules.yaml（闸门规则）、field_policy.yaml（字段敏感等级）
- `agent/`  买方 agent、独立脚本、不守规矩的 agent（BE3）
- `tests/`  测试 runner（BE3）；`test_protocol_rules.py` 协议修改判定的测试（`PYTHONPATH=backend pytest tests`）
- `frontend/` 前端（FE）
- `openapi.json`、`fixtures/` 由 scripts/export_contract.py 生成

商品数据文件不在仓库里：从 Valio 资源门户下载后放到 `data/`，再运行 BE2 的 `scripts/preprocess.py`。

## BE2 静态目录链路

`catalog/repository.py` 只读查询预处理 SQLite，按类别和结构化证据过滤；
`units.py` 按订购步长向上取整；`cards.py` 生成证据、可信度与提示；
`service.py` 把这些模块组合成最多 8 个单 SKU 候选。运行示例（仓库根目录）：

```bash
PYTHONPATH=backend .venv/bin/python - <<'PY'
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from app.catalog.service import CatalogService, effective_rules
from app.schemas import ProtocolBody, Quantity, Requirement, StructuredRequest

today = datetime.now(ZoneInfo("Europe/Helsinki")).date()
lactose_free = Requirement(kind="CLAIM", code="FREE_FROM_LACTOSE")
protocol = ProtocolBody(
    buyer_id="buyer", agent_id="agent", approver="manager",
    auto_approve_limit_cents=100_000, max_order_cents=300_000,
    allowed_categories=["10400"], hard_requirements=[lactose_free],
    valid_until=today + timedelta(days=30),
)
request = StructuredRequest(
    category="10400", requirements=[lactose_free],
    quantity=Quantity(value=1000, unit="L"), delivery_date=today + timedelta(days=2),
)
result = CatalogService(today=today).generate(request, effective_rules(protocol))
print([(c.lines[0].product_id, c.outcome_preview) for c in result.candidates])
print("unconvertible:", result.unconvertible_product_ids)
PY
```

`effective_rules(protocol, profile)` 是不写库的合并函数：保留协议要求并加入
profile 硬排除；profile 不能扩展协议类别或额度。传入的协议应由 BE1 验证过。
商品的更新时间在读取时重新计算可信等级，空声明与缺失过敏原保持未验证。
名称数量换算保留 `NAME_PARSE`，因此即使数量刚好覆盖也需要复核；无法换算时
`Conversion.qty_covered=None`，服务通过 `unconvertible_product_ids` 报告原因。
如果该商品同时违反硬约束，`blocked` 保留违规详情，其 `lines` 为空。

真实价格/库存通过 `CatalogService(valio=...)` 注入 `ValioApi` 实现，BE1 闸门通过
`gate=...` 注入 `GateApi`。动态查询仅覆盖缩小后的最多 8 个 SKU；未注入时
返回未知库存、空总价和复核预览。缺价格、缺押金报价、缺库存或名称推算不能
被一个 `AUTO` 闸门预览绕过。金额按整数分、逐行 half-up 计算，押金视为已计税的
独立收费；正式报价仍由 BE1 复核并签名。

排序默认使用保守的稳定顺序（可信度、履约状态、总价、软偏好、商品 ID）。
`RankingPolicy(mode="weighted")` 可显式启用 profile 权重：价格/碳因子在候选内
min-max 归一化，未知值记中性 0.5；权重必须有限、非负且总和大于 0。
可靠性 HIGH/MEDIUM/LOW 阈值尚未冻结，因此默认显示 UNKNOWN；可向
`CardsBuilder` 提供审核后的 `reliability_high` 与 `reliability_medium`。
卡片与候选不会包含可靠性原始值；公开商品投影只输出结构化声明。

本轮模块可独立调用；`main.py` 仍使用 M1 fixtures。接入 HTTP/LLM 前还需要完成
`disclosure.py` 和 Valio 客户端，并在出口应用 `field_policy.yaml`。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend .venv/bin/python -m pytest -q tests
```
