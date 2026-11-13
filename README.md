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
- `tests/`  测试 runner（BE3）
- `frontend/` 前端（FE）
- `openapi.json`、`fixtures/` 由 scripts/export_contract.py 生成

商品数据文件不在仓库里：从 Valio 资源门户下载后放到 `data/`，再运行 BE2 的 `scripts/preprocess.py`。
