# PasStar · Yes We Cooked

**PasStar is Valio Aimo's ordering channel for AI purchasing agents.**

Hotels, hospitals and restaurants can let an AI assistant (ChatGPT, Claude, or their own ERP) buy from Valio on their behalf. Every agent must carry a key issued by the buyer and act within an **authorization protocol** signed by the buyer and confirmed by Valio: what it may buy, how much it may spend on its own, and who approves the rest. Every call goes through the same gate; anything outside the protocol is refused and reported to Valio, and every action is written to a tamper-evident audit log.

- Products: Valio's real catalogue, 17,546 products
- Prices and deliverability: Valio's live API
- Understanding requests: an LLM (via OpenRouter), cross-checked by rules
- Ordering: quotes signed by Valio (Ed25519), checked against the protocol at the gate

---

## Try it

**Live: https://passtar-demo.fly.dev** (the demo passcode is in our submission). Three minutes:

1. **Reset demo** (bottom of the sidebar), then step 0: write the rules and sign them
   > Milk, cream and milk powder. Everything must be lactose-free. Order on your own up to 1,000 euros, never above 3,000 euros. I approve anything in between myself.
2. Switch to **Valio**, review the protocol, confirm it; switch back to **You**
3. Tell Aimo: `300 litres of lactose-free milk for Wednesday`. Real Valio data gives a ✓ Reliable, a ? Reliable? and a ✗ Risky option side by side; open **Show details** on each, and the **Decision chain** above them
4. **Agents** → **Connect an agent** → ChatGPT → **Try it in the console**: watch an external agent order, and press **Try to break the rules**

---

## How PasStar answers the challenge

| Criterion | What we built | Evidence |
|---|---|---|
| **Working end-to-end demo (30%)** | Step 0 protocol signed by the buyer and Valio → request in plain words → four-step decision chain on Valio's real catalogue and live prices → Valio-signed quote → gate → order or approval → tamper-evident audit. The same path for people on the screens and for agents over MCP | Live app above; `backend/app/main.py`; [`backend/app/orchestrator/`](backend/app/orchestrator/); 533 automated tests (`pytest`) |
| **Solving the problem and user need (20%)** | A kitchen says what it needs in its own words; Aimo asks instead of guessing; every option shows price, delivery, a traffic-light trust check with scores, where each fact comes from, and plain-language tips (storage, deposits, certifications) | [`frontend/src/pages/RequestPage.tsx`](frontend/src/pages/RequestPage.tsx), [`OptionRow.tsx`](frontend/src/components/OptionRow.tsx), [`catalog/trust.py`](backend/app/catalog/trust.py). Evaluation runner (`python tests/runner.py --in-process`), 5 scenarios × 5 phrasings: LLM + rule cross-check **25/25 pass, 0 silent wrong orders**; the rule parser alone 56–72%, 5 silent wrong orders |
| **Autonomy and controls (20%)** | Agents act only under a protocol both sides signed (Ed25519); orders up to the limit go through, bigger ones wait for a person, anything outside is refused. No approval tool for agents; keys per agent, shown once, revocable; Valio can block an agent; agents see at most L1 data | [`channel/protocols.py`](backend/app/channel/protocols.py), [`channel/gate.py`](backend/app/channel/gate.py), [`channel/agents.py`](backend/app/channel/agents.py), [`orchestrator/mcp_server.py`](backend/app/orchestrator/mcp_server.py), [`config/field_policy.yaml`](backend/app/config/field_policy.yaml); the console's **Try to break the rules** (forged key, self-approval, over the maximum, another buyer's data: all refused and reported) |
| **Robustness to exception situations (15%)** | Missing quantity or date → a question; nothing deliverable → the first date that works; price changed → re-quote; partial stock, unknown stock, cut-offs; probe limit against stock fishing; Valio timeouts and 429 retried; a shopping list in one message → handled one product at a time; no LLM → rule parser and templates | [`orchestrator/parser.py`](backend/app/orchestrator/parser.py), [`orchestrator/agent_api.py`](backend/app/orchestrator/agent_api.py), [`valio/client.py`](backend/app/valio/client.py), [`valio/faults.py`](backend/app/valio/faults.py) (fault switches on Valio's Agents page), [`orchestrator/several.py`](backend/app/orchestrator/several.py) |
| **Openness and credible path toward production (15%)** | One open interface for any agent: MCP (ChatGPT and Claude plugins), REST with OpenAPI, and an ERP path with the buyer's own signed authorization. Verifiable: Valio-signed quotes and protocols (JWKS published), hash-chained audit log with a verify endpoint. Deployed on Fly.io as one container | [`openapi.json`](openapi.json), [`plugin/`](plugin/), `GET /v1/.well-known/jwks.json`, `GET /v1/quotes/{id}/verify`, `GET /v1/audit/verify`, [`Dockerfile`](Dockerfile), [`fly.toml`](fly.toml) |

## Quick start

Requires **Python 3.11+** and **Node.js 18+**.

### 1. Backend

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

Create a `.env` in the repository root (git-ignored, **never commit it**), following `.env.example`:

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

The API docs are at http://localhost:8000/docs.

> After changing `.env`, restart the backend yourself: `--reload` restarts on code changes only and does not re-read environment variables.

### 2. Product catalogue (optional, for real data)

Download the three data files from the Valio resource portal and build the local catalogue `backend/data/products.sqlite`:

```bash
python backend/scripts/preprocess.py --source-dir <folder with the data files>
```

**Without the catalogue or a Valio key the backend falls back to demo data** and every flow still works. Demo data is labelled `DEMO_DATA` on the screens and in the agent's answers, so it is never passed off as Valio's.

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173.

---

## Demo accounts

There is no login on the screens; switch accounts at the top of the sidebar (the `X-Demo-User` header, accepted on the local machine only, or on the public deployment after the demo passcode). The demo shows two accounts:

| Account | Workspace | Can |
|---|---|---|
| **You** · Restaurant owner | Bistro Kallio | set the rules (step 0), request, order, approve your own bigger orders (logged as a separate step), connect AI agents |
| **Valio** · Reviewer | Valio | confirm protocols, review orders, see every agent, block agents |

The restaurant starts without a protocol: after **Reset demo**, step 0 guides you through setting the rules.

A second approval mode, a hotel where the chef requests and a manager approves (nobody approves their own order), is in the code and the backend (`usr_chef`, `usr_manager`) but not offered in the switcher (`SHOWN_ROLES` in `frontend/src/config/accounts.ts`).

---

## Demo flow

**Main line (screens)**
1. **Reset demo** → step 0: describe the rules in one sentence → Aimo drafts the protocol → check it (edit the draft in place) → sign
2. Switch to **Valio**, review the protocol and confirm it in the Review queue
3. Back as **You**, make a request: chat with Aimo on the left or fill in the form on the right; either finds options directly
4. Read the options: traffic-light trust (✓ Reliable / ? Reliable? / ✗ Risky), scores, data sources, and what choosing each one does
5. Order: up to €1,000 is placed at once; €1,000–3,000 waits for approval; above €3,000 is refused

**AI agents (Agents page)**
1. **Agents** → **Connect an agent**: choose ChatGPT / Claude / ERP → issue the key (shown once) → follow the generated setup → **Try it in the console**
2. **Console**: the conversation the user sees on the left; everything PasStar did behind it on the right (key check, the four steps, the gate, Valio's signature, the audit entries)
3. **Try to break the rules**: the agent really attempts four violations, and each one is refused
4. **My orders**: orders placed by agents appear on their own, labelled with the agent that placed them

---

## Connecting AI agents

An agent's only way in is the **MCP endpoint `POST /mcp`**, with its key in the `X-Agent-Key` header. It offers six tools, and **no approval tool**:

| Tool | What it does |
|---|---|
| `get_mandate` | who I act for, what I may buy, the limits, the approver, how to read results, the rules I must follow |
| `find_options` | the user's words plus answers to earlier questions → a question, or ranked options (each with a card) |
| `get_options` | the options of an earlier request again |
| `adjust_options` | allow a brand, exclude a product or option, rank by price / reliability, one product only, force approval |
| `place_order` | order the chosen option; calling it again for the same option returns the same order |
| `order_status` | an order's status, and what to tell the user next |

**ChatGPT plugin**: the package is in `plugin/passtar/` (`plugin.json`, `mcp.json` and the skill). Build it with the public address and the key:

```bash
cloudflared tunnel --url http://localhost:8000          # in another window, keep it open
read -s "THE_PASS_AGENT_KEY?Agent key: "; export THE_PASS_AGENT_KEY
plugin/build.sh https://xxxx.trycloudflare.com          # builds plugin/passtar.zip (not in git)
```

Then upload `passtar.zip` in the ChatGPT desktop app. See `plugin/README.md`.

**Claude Code**:

```bash
claude mcp add --transport http passtar https://xxxx.trycloudflare.com/mcp --header "X-Agent-Key: <key>"
```

**Where keys come from**
- Screens: Agents → Connect an agent → Issue the key (only the protocol's approver can)
- Command line: `python backend/scripts/new_agent_keys.py agt_hotel`
- Built-in demo keys: `demo-hotel`, `demo-bistro` and so on. They are public in the code, so **use them on the local machine only**

---

## Security design

- **People sign in on the local machine only**: an `X-Demo-User` request that came through a tunnel or proxy is refused. From outside, only agent keys work.
- **Admin routes are local only**: reset and the fault switches refuse anything that came through a tunnel.
- **Keys are stored as hashes**: shown once when issued; the buyer can disconnect an agent (revoke its key), and Valio can block an agent, after which even a valid key is refused.
- **Sensitivity levels**: agents see at most L1 (their own prices, deliverable yes/no). L2 (stock levels, supplier performance) and L3 (costs, margins, other customers) are never shown to them.
- **Probe limit**: an agent may check at most three different quantities of the same product per day. Beyond that it gets `PROBE_LIMIT`, and Valio is told.
- **The agent console is for the buyer's people only**, and only on the local machine, so nobody can spend our LLM budget through the tunnel.

---

## Deployment

The live demo runs on **Fly.io** as one container that serves the screens, the API and `/mcp` from one address ([`Dockerfile`](Dockerfile), [`fly.toml`](fly.toml)); the database is on a persistent volume.

```bash
fly secrets set VALIO_API_BASE_URL=… VALIO_API_KEY=… VALIO_BUYER_ID=… VALIO_CUSTOMER_ID=… OPENROUTER_API_KEY=… \
  PASSTAR_SESSION_SECRET=… PASSTAR_DEMO_PASSCODE=… --app passtar-demo
fly deploy --app passtar-demo --remote-only
```

`PASSTAR_PUBLIC=1` (set in `fly.toml`) switches on public mode: people enter the demo passcode once instead of being on the local machine, the demo agent keys written in the code are replaced by random ones, and a signed-in session may reset the demo. The catalogue (`backend/data/products.sqlite`) goes into the image from the local folder; it is never in git.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Address already in use` | `lsof -i :8000` to find the process, then `kill <pid>` |
| The agent reports `PROBE_LIMIT` | Too many different quantities of these products today. **Reset demo** clears the counts; in a demo, keep to one quantity |
| ChatGPT says "Unknown or blocked agent key" | The key was reset or replaced. Issue a new key, rebuild and re-upload the plugin |
| ChatGPT cannot connect | The cloudflared address changes on every restart; rebuild the plugin with the new address |
| Agents stop working after a reset | Reset demo restores the public demo keys, so every key you issued stops working |
| The console's Live model says there is no key | Add `OPENROUTER_API_KEY` to `.env` and **restart the backend** |
| The frontend cannot find a font | Run `npm install` in `frontend` after pulling, then restart `npm run dev` |

---

## Layout

```
backend/app/
  main.py            routes (every HTTP endpoint)
  schemas.py         the API contract (Pydantic)
  internal.py        interfaces between backend modules
  schema.sql         database tables
  channel/           BE1: protocols, quotes, gate, orders, approvals, audit, agents and keys
  catalog/           BE2: catalogue, unit conversion, trust grades, evidence cards, disclosure filter
  valio/             BE2: Valio API client, probe budget, fault simulation
  orchestrator/      BE3: request parsing, the four steps, ranking, explanations, MCP server, agent API, console
  config/            rules.yaml (gate rules), field_policy.yaml (field sensitivity)
backend/scripts/     preprocess.py (catalogue), new_agent_keys.py, buyer_erp.py, export_contract.py
frontend/            React frontend
plugin/              ChatGPT / Claude plugin package and build script
agent/               scripted agents: a normal buyer, a script without AI, a rule-breaking agent
tests/               backend tests and the evaluation runner
openapi.json, fixtures/   generated by backend/scripts/export_contract.py
```

**Not in the repository**: `.env`, `backend/data/*.sqlite` (catalogue and runtime data), `plugin/*.zip`, Valio's raw data.

---

## Tests

```bash
.venv/bin/python -m pytest -q                       # all backend tests
cd frontend && npx tsc -b --noEmit && npm run build  # frontend type check and build
```

Tests never call a real LLM or the Valio API, and each test gets its own temporary databases.

Evaluation runner (real model, several phrasings, repeated runs, injected faults):

```bash
python tests/runner.py --help
```

---

## Technical notes

### The four steps of every request

1. **Understand**: an LLM reads the user's words and rules cross-check it; a missing product, quantity or date is asked about first
2. **Filter**: the protocol's hard requirements and the kitchen profile filter products; every requirement gets an A–D trust grade
3. **Rank**: one batched call to Valio for prices and deliverability, pack-size conversion, at most eight options
4. **Gate preview**: each option says what choosing it would do (place at once / wait for approval / Valio review / not allowed)

Any step can be **retried**, and only that step onward is redone. Each step can be retried at most three times, and a retry never loosens the protocol.

### Trust grades and scores

| Grade | Meaning |
|---|---|
| A | The manufacturer's structured data, updated within 24 months |
| B | The manufacturer's data, older than 24 months |
| C | Only the product name says so; not verified |
| D | No data, or the data contradicts itself |

The **trust check** has four parts: *product info verified* (evidence), *matches your request* (match), *arrives on time* (fulfilment) and *within your rules* (compliance). **The overall score is the lowest of the four.** A score is the average of that part's checks, always within its level's band: Reliable 80–100, Reliable? 50–79, Risky 0–49.

### Authorization protocols

- The buyer drafts it (Aimo can draft it from one sentence) → the buyer's approver signs → Valio confirms and signs it with Ed25519, and only then is it in force
- **Tightening** (e.g. a lower limit) applies at once; **loosening** creates a new version that must be signed again and confirmed again by Valio
- An agent may help draft a protocol, but it can neither put one in force nor widen its own permissions
- Over-limit orders are approved in PasStar (`HOSTED`) or authorized with a signature from the buyer's own system (`EXTERNAL`); see `backend/app/channel/authorization.py`

### Valio data

- The challenge provides one Valio customer account; the three demo buyers all price as that account (`VALIO_SHARED_BUYERS`)
- The challenge environment has no endpoint for submitting orders: `PLACED` means PasStar released the order; in production it would be handed to Valio's order system
- Prices and stock are queried for at most eight shortlisted products; a missing price, VAT rate or deposit is never counted as zero
- Local rate limit of 30 calls per minute; 429, 5xx and timeouts are retried a limited number of times
- Fault simulation (Valio's Agents & faults page): `PRICE_CHANGE`, `PARTIAL_STOCK`, `TIMEOUT`, `RATE_LIMIT`, `EMPTY_RESULT`

### Environment variables

| Variable | Purpose |
|---|---|
| `VALIO_API_BASE_URL`, `VALIO_API_KEY`, `VALIO_BUYER_ID`, `VALIO_CUSTOMER_ID` | Valio API |
| `OPENROUTER_API_KEY` | LLM. Without it, parsing falls back to rules, explanations to templates, and the console to script mode |
| `THE_PASS_LLM_STRONG`, `THE_PASS_LLM_LIGHT`, `THE_PASS_LLM_AGENT` | Choose models (defaults: Opus 5.5 for parsing, Gemini Flash Lite for explanations) |
| `THE_PASS_BE2` | `auto` (default) / `live` / `stub`: force real or demo data |
| `VALIO_SHARED_BUYERS` | Which buyers price as the one Valio account |
| `THE_PASS_DB` | Database path (default `data/the_pass.db`) |
| `PASSTAR_SELF_URL` | The address the console's agent uses to reach PasStar (default `http://127.0.0.1:8000`) |
