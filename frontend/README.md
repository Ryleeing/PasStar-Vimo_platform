# Frontend (FE) · PasStar

React 18 + Vite + TypeScript. Implements the 8 pages in section 7 of the technical design document.

## Running

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

Backend (optional): `cd backend && uvicorn app.main:app --reload` (http://localhost:8000).

## Data source: Live / Mock

Switch it at the bottom left of the sidebar, or set a default in `.env.local` (see `.env.example`):

| Mode | Behaviour |
|---|---|
| `auto` (default) | Calls the backend first; falls back to the in-browser mock when the backend cannot be reached |
| `live` | Backend only; errors are shown in the UI |
| `mock` | Never calls the backend; everything comes from the mock |

The mock (`src/api/mock/`) reads `fixtures/*.json` from the repo root directly, the same data as the fake data server. It keeps state (selected quote, orders, approval decisions, fault switches, audit log), so "chef submits → manager approves → log grows" can be demoed end to end without a backend.

## How it connects to the backend

- `src/api/schema.d.ts`: generated from `openapi.json`, **do not edit by hand**. After the backend changes `schemas.py` and re-exports `openapi.json`, run `npm run gen:types`.
- `src/api/types.ts`: aliases for the generated types (`Candidate`, `RequestResult`…).
- `src/api/endpoints.ts`: one function per backend route; pages only call these.
- `src/api/client.ts`: always sends the `X-Demo-User` header; turns `{"error": {code, message, details}}` into an `ApiError`; `describeError()` turns error codes into plain language.
- `Idempotency-Key` for `POST /v1/orders`: generated once per quote and reused on retry, so an order is never placed twice.

## Accounts (role switching)

The account switcher at the top of the sidebar is grouped by organisation; switching account = switching `X-Demo-User`:

| Organisation | Mode | Accounts |
|---|---|---|
| Hotel Helsinki Central | SEPARATE | Aino · Chef (`usr_chef`), Mikko · Procurement manager (`usr_manager`) |
| Café Kallio | SELF | Sanna · Owner (`usr_owner`) |
| Valio Aimo | — | Elina · Valio reviewer (`usr_valio`) |

Switching accounts within the same organisation keeps the current order's progress (after the chef submits, switch to the manager to approve); switching to another buyer organisation starts over. The mapping between accounts and pages lives in `src/config/accounts.ts`.

## Layout

```
src/
  api/          client, endpoints, generated types, mock
  config/       accounts, organisations, the pages each role can see
  state/        session (account, theme, data source), flow (current order progress)
  components/   Sidebar (account + navigation + progress), ChainView (processing chain), CandidateCard, CheckoutBar (signed quote), ui (base components)
  pages/        Request, Options, Orders, Approvals, Protocol, Profile, Audit, ValioQueue, Agents
  styles/app.css design tokens (dark by default + light) and all styles
```

## Where we still depend on the M1 fake data server (goes away once the backend is implemented)

- `/requests/{id}/select` always returns cand_1's quote and an AUTO decision, so in live mode an over-limit option still shows "Place order".
- The backend only has the hotel protocol (SEPARATE); in one-person mode (Café Kallio, `usr_owner`) approving in live mode returns `NOT_APPROVER`. BE1 needs to add a SELF protocol and matching approval to the seed data.
- `/approvals` does not filter by `kind`/`status`, so the frontend filters again in `endpoints.ts`.
- The agent list on the "Agents & faults" page has no endpoint; it is placeholder data hard-coded in the frontend.
