# 前端（FE）· PasStar

React 18 + Vite + TypeScript。对应技术设计文档第 7 节的 8 个页面。

## 运行

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

后端（可选）：`cd backend && uvicorn app.main:app --reload`（http://localhost:8000）。

## 数据来源：Live / Mock

侧栏左下角可以切换，也可以用 `.env.local` 设默认值（见 `.env.example`）：

| 模式 | 行为 |
|---|---|
| `auto`（默认） | 先调后端；后端连不上时自动退回浏览器内的 mock |
| `live` | 只调后端，错误直接显示在界面上 |
| `mock` | 不调后端，全部用 mock |

mock（`src/api/mock/`）直接读仓库根目录的 `fixtures/*.json`，和假数据服务器同一份数据；它会记住状态（选中的报价、订单、审批决定、故障开关、审计日志），所以“厨师提交 → 经理审批 → 日志增加”在没有后端时也能完整演示。

## 和后端怎么接

- `src/api/schema.d.ts`：由 `openapi.json` 生成，**不要手改**。后端改了 `schemas.py` 并重新导出 `openapi.json` 后运行 `npm run gen:types`。
- `src/api/types.ts`：生成类型的别名（`Candidate`、`RequestResult`…）。
- `src/api/endpoints.ts`：每个后端路由一个函数，页面只调用这里。
- `src/api/client.ts`：统一带 `X-Demo-User` 头；把 `{"error": {code, message, details}}` 转成 `ApiError`；`describeError()` 把错误码翻成人话。
- `POST /v1/orders` 的 `Idempotency-Key`：每个报价生成一次，重试复用，不会下两张单。

## 账号（角色切换）

侧栏顶部的账号切换器按组织分组，切换账号 = 切换 `X-Demo-User`：

| 组织 | 模式 | 账号 |
|---|---|---|
| Hotel Helsinki Central | SEPARATE | Aino · Chef（`usr_chef`）、Mikko · Procurement manager（`usr_manager`） |
| Café Kallio | SELF | Sanna · Owner（`usr_owner`） |
| Valio Aimo | — | Elina · Valio reviewer（`usr_valio`） |

同一组织内切换账号保留当前订单进度（厨师提交后切到经理就能审批）；换到另一个买家组织会重新开始。账号和页面的对应关系在 `src/config/accounts.ts`。

## 目录

```
src/
  api/          client、endpoints、生成的类型、mock
  config/       账号、组织、每个角色能看到的页面
  state/        session（账号、主题、数据源）、flow（当前订单进度）
  components/   Sidebar（账号 + 导航 + 进度）、ChainView（处理链）、CandidateCard、CheckoutBar（签名报价）、ui（基础组件）
  pages/        Request、Options、Orders、Approvals、Protocol、Profile、Audit、ValioQueue、Agents
  styles/app.css 设计变量（深色默认 + 浅色）和全部样式
```

## 目前依赖 M1 假数据服务器的地方（后端实现后自然消失）

- `/requests/{id}/select` 总是返回 cand_1 的报价和 AUTO 判断，所以 live 模式下选超额方案也显示“Place order”。
- 后端只有酒店协议（SEPARATE）；单人模式（Café Kallio、`usr_owner`）在 live 模式下审批会得到 `NOT_APPROVER`。需要 BE1 在种子数据里加一份 SELF 协议和对应审批。
- `/approvals` 不按 `kind`/`status` 过滤，前端在 `endpoints.ts` 里又过滤了一次。
- “Agents & faults” 页的 agent 列表没有接口，是前端写死的示意数据。
