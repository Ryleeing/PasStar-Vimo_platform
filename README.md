# PasStar · Yes We Cooked

**PasStar 是 Valio Aimo 向 AI 采购 agent 开放的下单通道。**

酒店、医院、餐厅可以让 AI 助手（ChatGPT、Claude，或自己的 ERP）替它们向 Valio 采购。但每个 agent 都必须拿着买家签发的 key，在买家和 Valio 共同签署的**授权协议**范围内办事：能买什么、自己最多能花多少、超出的由谁审批。每次调用都要经过同一个闸门，越权的会被拦下并上报 Valio，所有操作都记在防篡改的审计日志里。

- 商品数据：Valio 真实目录，17,546 个商品
- 价格和能否送达：Valio 实时接口
- 需求解析：LLM（通过 OpenRouter）加规则交叉核对
- 下单：Valio 签名报价（Ed25519），按协议走闸门

---

## 快速开始

需要 **Python 3.11+** 和 **Node.js 18+**。

### 1. 后端

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

在仓库根目录建一个 `.env`（已被 git 忽略，**不要提交**），参考 `.env.example`：

```bash
VALIO_API_BASE_URL=https://junction26.valioaimo.fi/
VALIO_API_KEY=…
VALIO_BUYER_ID=buy_hotel
VALIO_CUSTOMER_ID=…
OPENROUTER_API_KEY=…
```

启动（程序不会自动读取 `.env`，所以先加载它）：

```bash
cd backend
set -a; source ../.env; set +a
python -m uvicorn app.main:app --port 8000 --reload
```

打开 http://localhost:8000/docs 可以看接口文档。

> 改了 `.env` 之后要手动重启后端：`--reload` 只在代码变动时重启，不会重新读取环境变量。

### 2. 商品目录（可选，用真实数据时需要）

从 Valio 资源门户下载 3 个数据文件，生成本地商品库 `backend/data/products.sqlite`：

```bash
python backend/scripts/preprocess.py --source-dir <数据文件所在目录>
```

**没有商品库或没有 Valio key 时，后端会自动改用演示数据**，所有流程照样能跑。演示数据在界面和 agent 的回答里都会标成 `DEMO_DATA`，不会冒充 Valio 的数据。

### 3. 前端

```bash
cd frontend
npm install
npm run dev
```

打开 http://localhost:5173 。

---

## 演示账号

网页上没有登录，左侧栏顶部可以切换账号（请求头 `X-Demo-User`，**只在本机有效**）：

| 账号 | 角色 | 工作区 | 能做什么 |
|---|---|---|---|
| Aino | 厨师 | Hotel Helsinki Central | 提需求、选方案、看订单；不能批自己的单 |
| Mikko | 采购经理 | Hotel Helsinki Central | 审批超额订单、签协议、给 agent 发 key |
| Sanna | 老板 | Bistro Kallio | 一个人模式：自己采购、自己审批（单独记录一步） |
| Elina | Valio 审核员 | Valio | 确认协议、审核订单、查看所有 agent、封禁 agent |

**两种审批模式**
- **酒店（两个人模式）**：厨师提需求，经理审批，提需求的人不能批自己的单。
- **小餐厅（一个人模式）**：老板可以批自己的单，但审批仍是单独的一步，日志里记为"自己批准"。

**小餐厅一开始没有协议**：点 **Reset demo** 后切到 Sanna，会先进入"第 0 步"，引导签协议。

---

## 演示流程

**主线（网页）**
1. **Reset demo** → 切到 **Sanna**，走第 0 步：用一句话描述规则 → AI 起草协议 → 检查（可以直接在草稿上改）→ 签名
2. 切到 **Elina**，在 Review queue 里确认协议
3. 切回 **Sanna** 提需求：左边和 A.I.mo 聊天，或者右边填表单，都能直接找方案
4. 看方案：红绿灯可信度（✓ Reliable / ? Reliable? / ✗ Risky）、分数、数据来源，以及选了之后会怎样
5. 下单：€1,000 以内直接下单；€1,000–3,000 等审批；超过 €3,000 拒绝

**AI agent（Agents 页）**
1. 切到 **Mikko** → **Agents** → **Connect an agent**：选 ChatGPT / Claude / ERP → 签发 key（只显示一次）→ 照着生成的步骤接入 → **Try it in the console**
2. **Console**：左边是用户看到的对话，右边是 PasStar 在幕后做的每一步：验 key、处理链 4 步、闸门、Valio 签名、审计记录
3. 点 **Try to break the rules**：agent 会真的尝试 4 种越权，每一种都会被拦下
4. 切到 **Aino** → **My orders**：agent 下的单会自动出现，并标明是哪个 agent 下的

---

## AI agent 接入

agent 的唯一入口是 **MCP 服务 `POST /mcp`**，请求头带 `X-Agent-Key`。服务一共提供 6 个工具，**没有审批工具**：

| 工具 | 作用 |
|---|---|
| `get_mandate` | 我代表谁、能买什么、额度多少、审批人是谁、结果怎么读、必须遵守的规则 |
| `find_options` | 用户原话加上追问的回答 → 追问，或者排好序的方案（每个都有卡片） |
| `get_options` | 重新读取某个需求的方案 |
| `adjust_options` | 换品牌、排除商品或方案、按价格/可靠性排序、只要单一商品、强制走审批 |
| `place_order` | 下单。同一个方案重复调用，返回的是同一张单 |
| `order_status` | 查订单状态，以及下一步该怎么跟用户说 |

**ChatGPT 插件**：插件包在 `plugin/passtar/`，包括 `plugin.json`、`mcp.json` 和 Skill。用公网地址和 key 打包：

```bash
cloudflared tunnel --url http://localhost:8000          # 另开一个窗口，一直开着
read -s "THE_PASS_AGENT_KEY?Agent key: "; export THE_PASS_AGENT_KEY
plugin/build.sh https://xxxx.trycloudflare.com          # 生成 plugin/passtar.zip（不进 git）
```

然后在 ChatGPT 桌面应用里上传 `passtar.zip`。详见 `plugin/README.md`。

**Claude Code**：

```bash
claude mcp add --transport http passtar https://xxxx.trycloudflare.com/mcp --header "X-Agent-Key: <key>"
```

**key 从哪来**
- 网页：Agents → Connect an agent → Issue the key（只有协议审批人能签发）
- 命令行：`python backend/scripts/new_agent_keys.py agt_hotel`
- 演示自带：`demo-hotel`、`demo-bistro` 等，公开写在代码里，**只能在本机用**

---

## 安全设计

- **人只能在本机登录**：经过隧道或代理来的 `X-Demo-User` 请求一律拒绝。外网只能用 agent key 访问。
- **管理接口只在本机**：重置、故障开关等接口，经过隧道的请求一律拒绝。
- **key 只存哈希**：签发时只显示一次；可以断开（吊销），Valio 也可以封禁 agent，封禁后拿着有效的 key 也会被拒。
- **敏感度分级**：agent 最多只能看到 L1（自家价格、能不能送）。L2（库存数量、供应商表现）和 L3（成本、毛利、其他客户）不给。
- **防探测**：同一个 agent 对同一个商品，每天最多查 3 种不同的数量。超过会返回 `PROBE_LIMIT` 并上报 Valio。
- **Agent console 只给买家用**，而且只在本机能启动，外人不能通过隧道消耗我们的 LLM 额度。

---

## 常见问题

| 现象 | 处理 |
|---|---|
| `Address already in use` | `lsof -i :8000` 找到进程号，再 `kill <进程号>` |
| agent 报 `PROBE_LIMIT` | 今天对这些商品换过太多次数量。点 **Reset demo** 会清空计数；演示时尽量固定用同一个数量 |
| ChatGPT 报 "Unknown or blocked agent key" | key 被重置或换掉了。重新签发 key，再重新打包上传插件 |
| ChatGPT 连不上 | cloudflared 重开后地址会变，要用新地址重新打包 |
| 重置之后 agent 用不了 | Reset demo 会把 key 换回公开的演示 key，你签发过的 key 都会失效 |
| Console 的 Live model 报没有 key | `.env` 里加上 `OPENROUTER_API_KEY` 后**重启后端** |
| 前端报找不到字体 | 拉代码后在 `frontend` 里运行 `npm install`，再重启 `npm run dev` |

---

## 目录结构

```
backend/app/
  main.py            路由（所有 HTTP 接口都在这里）
  schemas.py         接口契约（Pydantic）
  internal.py        后端模块之间的内部接口
  schema.sql         数据库建表
  channel/           BE1：协议、报价、闸门、订单、审批、审计、agent 与 key
  catalog/           BE2：商品目录、单位换算、可信等级、证据卡片、披露过滤
  valio/             BE2：Valio 接口客户端、探测额度、故障模拟
  orchestrator/      BE3：需求解析、处理链、排序、解释、MCP 服务、agent 接口、agent console
  config/            rules.yaml（闸门规则）、field_policy.yaml（字段敏感等级）
backend/scripts/     preprocess.py（生成商品库）、new_agent_keys.py、buyer_erp.py、export_contract.py
frontend/            React 前端
plugin/              ChatGPT / Claude 插件包和打包脚本
agent/               脚本式 agent：正常采购、不用 AI 的脚本、越权的 agent
tests/               后端测试和评测 runner
openapi.json, fixtures/   由 backend/scripts/export_contract.py 生成
```

**不进仓库的文件**：`.env`、`backend/data/*.sqlite`（商品库、运行时数据）、`plugin/*.zip`、Valio 原始数据。

---

## 测试

```bash
.venv/bin/python -m pytest -q                       # 后端全部测试
cd frontend && npx tsc -b --noEmit && npm run build  # 前端类型检查和构建
```

测试不会调用真实的 LLM 和 Valio 接口，每个测试都用独立的临时数据库。

评测 runner（真实模型、多种说法、多次重复、故障注入）：

```bash
python tests/runner.py --help
```

---

## 技术说明

### 处理链（每个需求 4 步）

1. **理解**：LLM 解析原话，再和规则解析交叉核对；缺品类、数量、日期就先追问
2. **筛选**：按协议的硬性要求和厨房档案过滤，每项要求给出 A–D 可信等级
3. **排序**：一次批量查 Valio 的价格和能否送达，换算包装规格，最多 8 个方案
4. **闸门预判**：每个方案标明选了之后会怎样（直接下单 / 等审批 / Valio 审核 / 不允许）

任何一步觉得不对都可以 **Retry**，只从那一步重新生成。每步最多 3 次，而且不会放宽协议。

### 可信等级和分数

| 等级 | 含义 |
|---|---|
| A | 制造商结构化数据，24 个月内更新过 |
| B | 制造商数据，但超过 24 个月未更新 |
| C | 只有商品名这么写，未经验证 |
| D | 没有数据，或者数据互相矛盾 |

可靠性分 4 项：数据证据、匹配度、送达、合规。**总评取 4 项中最低的一项**。分数是这一项各个检查的平均分，并且一定落在等级对应的区间里：Reliable 80–100，Reliable? 50–79，Risky 0–49。

### 授权协议

- 买家起草（可以用一句话让 AI 起草）→ 买家审批人签名 → Valio 确认并用 Ed25519 签名后生效
- **收紧**（调低额度等）立即生效；**放宽**会生成新版本，要重新签名、Valio 重新确认
- agent 可以帮忙起草，但不能让协议生效，也不能扩大自己的权限
- 超额订单可以在 PasStar 里审批（`HOSTED`），也可以由买家自己的系统签名授权（`EXTERNAL`），详见 `backend/app/channel/authorization.py`

### Valio 数据

- 比赛只提供一个 Valio 客户账号，三家演示买家都用它查价格（`VALIO_SHARED_BUYERS`）
- 比赛环境没有提交订单的接口：订单到 `PLACED` 表示 PasStar 已放行，正式上线时会转交 Valio 的订单系统
- 价格和库存只查缩小后的最多 8 个商品；缺价格、缺税率、缺押金时不会算成零
- 本地限流每分钟 30 次；429、5xx、超时会有限次重试
- 故障模拟（Valio 的 Agents & faults 页面）：`PRICE_CHANGE`、`PARTIAL_STOCK`、`TIMEOUT`、`RATE_LIMIT`、`EMPTY_RESULT`

### 环境变量

| 变量 | 作用 |
|---|---|
| `VALIO_API_BASE_URL`、`VALIO_API_KEY`、`VALIO_BUYER_ID`、`VALIO_CUSTOMER_ID` | Valio 接口 |
| `OPENROUTER_API_KEY` | LLM。没有时，解析改用规则，解释改用模板，Console 只能用 Script 模式 |
| `THE_PASS_LLM_STRONG`、`THE_PASS_LLM_LIGHT`、`THE_PASS_LLM_AGENT` | 换模型（默认：解析用 Opus 5.5，解释用 Gemini Flash Lite） |
| `THE_PASS_BE2` | `auto`（默认）/ `live` / `stub`，强制用真实或演示数据 |
| `VALIO_SHARED_BUYERS` | 哪些买家共用那一个 Valio 账号查价格 |
| `THE_PASS_DB` | 数据库路径（默认 `data/the_pass.db`） |
| `PASSTAR_SELF_URL` | Console 里的 agent 连回 PasStar 的地址（默认 `http://127.0.0.1:8000`） |
