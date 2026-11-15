# Frontend · PasStar

React 18 + Vite + TypeScript.

## Run

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

The backend runs separately (see the root README): `cd backend && python -m uvicorn app.main:app --port 8000 --reload`.

## Data source: live or mock

Switch at the bottom of the sidebar, or set the default in `.env.local` (see `.env.example`):

| Mode | Behaviour |
|---|---|
| `auto` (default) | Calls the backend; falls back to the in-browser mock when the backend cannot be reached |
| `live` | Backend only; errors show on screen |
| `mock` | Never calls the backend |

The mock (`src/api/mock/`) reads `fixtures/*.json` from the repository root and keeps state (the chosen quote, orders, approval decisions, fault switches, audit log), so the main flow can be shown without a backend. The agent pages (Agents, Console) and My orders need the backend.

## How it talks to the backend

- `src/api/schema.d.ts` is generated from `openapi.json`. **Do not edit it by hand.** After the backend's `schemas.py` changes and `openapi.json` is exported again (`backend/scripts/export_contract.py`), run `npm run gen:types`.
- `src/api/types.ts`: short names for the generated types (`Candidate`, `RequestResult`, …).
- `src/api/endpoints.ts`: one function per backend route; pages call only these.
- `src/api/client.ts`: sends `X-Demo-User` on every call, turns `{"error": {code, message, details}}` into `ApiError`, and `describeError()` puts error codes into words.
- `src/api/console.ts`: reads the agent console's event stream (server-sent events from a POST).
- `POST /v1/orders` sends an `Idempotency-Key` created once per quote and reused on retries, so a retry never places a second order.

## Accounts

The account switcher at the top of the sidebar groups accounts by organisation; switching account switches `X-Demo-User`:

| Organisation | Approval mode | Accounts |
|---|---|---|
| Hotel Helsinki Central | separate approver | Aino · Chef (`usr_chef`), Mikko · Procurement manager (`usr_manager`) |
| Bistro Kallio | one person | Sanna · Owner (`usr_owner`) |
| Valio Aimo | — | Elina · Valio reviewer (`usr_valio`) |

Switching within an organisation keeps the current order in progress (the chef submits, the manager approves); switching to another buyer starts over. Which account sees which pages is in `src/config/accounts.ts`.

A buyer workspace without an active protocol shows only the step 0 page (`OnboardingPage`) until the protocol is signed and confirmed by Valio.

## Layout

```
src/
  api/          client, endpoints, generated types, console stream, mock
  config/       accounts, organisations, the pages each role sees
  state/        session (account, theme, data source), flow (the order in progress), protocol (step 0)
  components/   Sidebar, ChainView (the four steps), OptionRow, TrustMark (traffic-light trust), AgentConsole,
                Markdown (agent replies), CheckoutBar (signed quote), Mascot, ui (basic components)
  pages/        Onboarding (step 0), Request, Options, Review, Ordered, Orders, Approvals, ApproveReview,
                Protocol, Profile, Agents (connected agents, connect an agent, console), Audit, ValioQueue
  styles/app.css  design tokens (light and dark) and all styles
```
