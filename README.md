# PasStar · Yes We Cooked

**PasStar is the ordering channel Valio Aimo opens to AI purchasing agents.**

Hotels, hospitals and restaurants can let an AI assistant (ChatGPT, Claude, or their own ERP) buy from Valio on their behalf. But every agent must carry a key issued by the buyer and act within an **authorisation protocol** signed by both the buyer and Valio: what it may buy, how much it may spend on its own, and who approves anything beyond that. Every call passes through the same gate; anything out of bounds is blocked and reported to Valio, and every action is written to a tamper-evident audit log.

- Product data: Valio's real catalogue, 17,546 products
- Prices and deliverability: Valio's live API
- Request parsing: an LLM (via OpenRouter), cross-checked against rules
- Ordering: Valio-signed quotes (Ed25519), passed through the gate according to the protocol

---

## Quick start

Requires **Python 3.11+** and **Node.js 18+**.

### 1. Backend

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

Create a `.env` in the repo root (it is git-ignored, **do not commit it**), based on `.env.example`:

```bash
VALIO_API_BASE_URL=https://junction26.valioaimo.fi/
VALIO_API_KEY=…
VALIO_BUYER_ID=buy_hotel
VALIO_CUSTOMER_ID=…
OPENROUTER_API_KEY=…
```

Start it (the app does not read `.env` by itself, so load it first):

```bash
cd backend
set -a; source ../.env; set +a
python -m uvicorn app.main:app --port 8000 --reload
```

Open http://localhost:8000/docs for the API docs.

> After changing `.env`, restart the backend by hand: `--reload` only restarts on code changes and does not re-read environment variables.

### 2. Product catalogue (optional, needed for real data)

Download the 3 data files from the Valio resource portal and build the local product database `backend/data/products.sqlite`:

```bash
python backend/scripts/preprocess.py --source-dir <directory with the data files>
```

**Without the product database or a Valio key, the backend falls back to demo data automatically**, and every flow still works. Demo data is marked `DEMO_DATA` in both the UI and the agent's answers, so it never passes itself off as Valio data.

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 .

---

## Demo accounts

There is no login on the web app; switch accounts at the top of the left sidebar (request header `X-Demo-User`, **only works on localhost**):

| Account | Role | Workspace | What they can do |
|---|---|---|---|
| Aino | Chef | Hotel Helsinki Central | Make requests, pick options, see orders; cannot approve their own orders |
| Mikko | Procurement manager | Hotel Helsinki Central | Approve orders over the limit, sign protocols, issue agent keys |
| Sanna | Owner | Bistro Kallio | One-person mode: buys and approves on their own (approval logged as a separate step) |
| Elina | Valio reviewer | Valio | Confirm protocols, review orders, see all agents, block agents |

**Two approval modes**
- **Hotel (two-person mode)**: the chef makes the request, the manager approves it, and whoever made a request cannot approve it.
- **Small restaurant (one-person mode)**: the owner may approve their own orders, but approval is still a separate step, logged as "self-approved".

**The small restaurant starts with no protocol**: click **Reset demo** and switch to Sanna to enter "Step 0", which walks you through signing a protocol.

---

## Demo flow

**Main path (web)**
1. **Reset demo** → switch to **Sanna** and do Step 0: describe the rules in one sentence → AI drafts the protocol → review it (you can edit the draft directly) → sign
2. Switch to **Elina** and confirm the protocol in the Review queue
3. Switch back to **Sanna** and make a request: chat with A.I.mo on the left or fill in the form on the right; either one finds options directly
4. Look at the options: traffic-light reliability (✓ Reliable / ? Reliable? / ✗ Risky), scores, data sources, and what happens if you pick each one
5. Order: up to €1,000 orders straight away; €1,000–3,000 waits for approval; over €3,000 is refused

**AI agents (Agents page)**
1. Switch to **Mikko** → **Agents** → **Connect an agent**: choose ChatGPT / Claude / ERP → issue a key (shown only once) → follow the generated steps to connect → **Try it in the console**
2. **Console**: the left side is the conversation the user sees; the right side is every step PasStar takes behind the scenes: key check, the 4-step chain, the gate, Valio's signature, the audit record
3. Click **Try to break the rules**: the agent really attempts 4 kinds of overreach, and every one is blocked
4. Switch to **Aino** → **My orders**: orders placed by agents show up automatically, labelled with which agent placed them

---

## Connecting an AI agent

The agent's only entry point is the **MCP service `POST /mcp`**, with an `X-Agent-Key` request header. The service offers 6 tools and **no approval tool**:

| Tool | What it does |
|---|---|
| `get_mandate` | Who I act for, what I may buy, my limit, who the approver is, how to read results, the rules I must follow |
| `find_options` | The user's own words plus answers to follow-up questions → a follow-up question, or ranked options (each with a card) |
| `get_options` | Re-read the options for a request |
| `adjust_options` | Change brand, exclude products or options, sort by price/reliability, single product only, force approval |
| `place_order` | Place an order. Calling it again for the same option returns the same order |
| `order_status` | Check an order's status, and what to tell the user next |

**ChatGPT plugin**: the plugin package lives in `plugin/passtar/`, with `plugin.json`, `mcp.json` and a Skill. Package it with a public URL and a key:

```bash
cloudflared tunnel --url http://localhost:8000          # in another window, keep it running
read -s "THE_PASS_AGENT_KEY?Agent key: "; export THE_PASS_AGENT_KEY
plugin/build.sh https://xxxx.trycloudflare.com          # builds plugin/passtar.zip (not in git)
```

Then upload `passtar.zip` in the ChatGPT desktop app. See `plugin/README.md` for details.

**Claude Code**:

```bash
claude mcp add --transport http passtar https://xxxx.trycloudflare.com/mcp --header "X-Agent-Key: <key>"
```

**Where keys come from**
- Web: Agents → Connect an agent → Issue the key (only the protocol's approver can issue one)
- Command line: `python backend/scripts/new_agent_keys.py agt_hotel`
- Built-in demo keys: `demo-hotel`, `demo-bistro` and so on, public in the code, **localhost only**

---

## Security design

- **People can only sign in on localhost**: any `X-Demo-User` request arriving through a tunnel or proxy is refused. From outside, only agent keys work.
- **Admin endpoints are localhost only**: reset, fault switches and similar endpoints refuse any request that came through a tunnel.
- **Only key hashes are stored**: a key is shown once when issued; it can be disconnected (revoked), and Valio can block an agent, after which even a valid key is refused.
- **Sensitivity levels**: an agent sees at most L1 (its own prices, whether it can be delivered). L2 (stock levels, supplier performance) and L3 (costs, margins, other customers) are never given out.
- **Anti-probing**: one agent may check at most 3 different quantities per product per day. Beyond that it gets `PROBE_LIMIT` and Valio is notified.
- **The agent console is for buyers only** and can only be started on localhost, so outsiders cannot spend our LLM quota through the tunnel.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Address already in use` | Find the process with `lsof -i :8000`, then `kill <pid>` |
| Agent reports `PROBE_LIMIT` | Too many different quantities were tried for these products today. **Reset demo** clears the count; during a demo, try to stick to one quantity |
| ChatGPT says "Unknown or blocked agent key" | The key was reset or replaced. Issue a new key, then repackage and re-upload the plugin |
| ChatGPT cannot connect | The cloudflared URL changes when it restarts; repackage with the new URL |
| Agent stops working after a reset | Reset demo swaps keys back to the public demo keys, so every key you issued stops working |
| Console's Live model says there is no key | Add `OPENROUTER_API_KEY` to `.env`, then **restart the backend** |
| Frontend cannot find a font | After pulling, run `npm install` in `frontend`, then restart `npm run dev` |

---

## Project layout

```
backend/app/
  main.py            Routes (every HTTP endpoint is here)
  schemas.py         API contract (Pydantic)
  internal.py        Internal interfaces between backend modules
  schema.sql         Database tables
  channel/           BE1: protocols, quotes, gate, orders, approvals, audit, agents and keys
  catalog/           BE2: product catalogue, unit conversion, trust grades, evidence cards, disclosure filtering
  valio/             BE2: Valio API client, probe limits, fault simulation
  orchestrator/      BE3: request parsing, processing chain, ranking, explanations, MCP service, agent API, agent console
  config/            rules.yaml (gate rules), field_policy.yaml (field sensitivity levels)
backend/scripts/     preprocess.py (builds the product database), new_agent_keys.py, buyer_erp.py, export_contract.py
frontend/            React frontend
plugin/              ChatGPT / Claude plugin package and build script
agent/               Scripted agents: a normal buyer, a script without AI, a rogue agent
tests/               Backend tests and the evaluation runner
openapi.json, fixtures/   Generated by backend/scripts/export_contract.py
```

**Files not in the repo**: `.env`, `backend/data/*.sqlite` (product database, runtime data), `plugin/*.zip`, Valio's raw data.

---

## Tests

```bash
.venv/bin/python -m pytest -q                       # all backend tests
cd frontend && npx tsc -b --noEmit && npm run build  # frontend type check and build
```

Tests never call the real LLM or the Valio API, and every test uses its own temporary database.

Evaluation runner (real model, many phrasings, repeated runs, fault injection):

```bash
python tests/runner.py --help
```

---

## Technical notes

### Processing chain (4 steps per request)

1. **Understand**: the LLM parses the user's words, cross-checked against rule-based parsing; if the category, quantity or date is missing, it asks first
2. **Filter**: filter by the protocol's hard requirements and the kitchen profile, giving each requirement an A–D trust grade
3. **Rank**: one batched lookup of Valio prices and deliverability, convert pack sizes, at most 8 options
4. **Gate preview**: each option shows what happens if you pick it (orders straight away / waits for approval / Valio review / not allowed)

If any step looks wrong you can **Retry** it, which regenerates from that step only. Each step allows at most 3 attempts and never loosens the protocol.

### Trust grades and scores

| Grade | Meaning |
|---|---|
| A | Manufacturer's structured data, updated within 24 months |
| B | Manufacturer's data, but not updated for more than 24 months |
| C | Only the product name says so, not verified |
| D | No data, or the data contradicts itself |

Reliability has 4 parts: data evidence, match, delivery, compliance. **The overall rating is the lowest of the 4.** Each part's score is the average of its checks and always falls inside its grade's band: Reliable 80–100, Reliable? 50–79, Risky 0–49.

### Authorisation protocol

- The buyer drafts it (AI can draft it from one sentence) → the buyer's approver signs → it takes effect once Valio confirms and signs it with Ed25519
- **Tightening** (lowering a limit and so on) takes effect immediately; **loosening** creates a new version that must be re-signed and re-confirmed by Valio
- An agent can help draft, but it cannot make a protocol take effect or widen its own permissions
- Orders over the limit can be approved inside PasStar (`HOSTED`) or authorised by a signature from the buyer's own system (`EXTERNAL`); see `backend/app/channel/authorization.py`

### Valio data

- The hackathon provides one Valio customer account, and all three demo buyers use it to look up prices (`VALIO_SHARED_BUYERS`)
- The hackathon environment has no order-submission API: an order reaching `PLACED` means PasStar released it; in production it would be handed to Valio's order system
- Prices and stock are looked up only for the narrowed-down set of at most 8 products; a missing price, VAT rate or deposit is never counted as zero
- Local rate limit of 30 calls per minute; 429, 5xx and timeouts get a limited number of retries
- Fault simulation (Valio's Agents & faults page): `PRICE_CHANGE`, `PARTIAL_STOCK`, `TIMEOUT`, `RATE_LIMIT`, `EMPTY_RESULT`

### Environment variables

| Variable | Purpose |
|---|---|
| `VALIO_API_BASE_URL`, `VALIO_API_KEY`, `VALIO_BUYER_ID`, `VALIO_CUSTOMER_ID` | Valio API |
| `OPENROUTER_API_KEY` | LLM. Without it, parsing falls back to rules, explanations to templates, and the Console only offers Script mode |
| `THE_PASS_LLM_STRONG`, `THE_PASS_LLM_LIGHT`, `THE_PASS_LLM_AGENT` | Change models (default: Opus 5.5 for parsing, Gemini Flash Lite for explanations) |
| `THE_PASS_BE2` | `auto` (default) / `live` / `stub`, forces real or demo data |
| `VALIO_SHARED_BUYERS` | Which buyers share the one Valio account for price lookups |
| `THE_PASS_DB` | Database path (default `data/the_pass.db`) |
| `PASSTAR_SELF_URL` | The address the Console's agent uses to call back into PasStar (default `http://127.0.0.1:8000`) |
