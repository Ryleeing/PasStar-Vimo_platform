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
