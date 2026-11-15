// HTTP client for the FastAPI backend (/v1).
// - Every request carries X-Demo-User (the account picked in the sidebar).
// - Errors are normalised from the backend's {"error": {code, message, details}} shape into ApiError.
// - Mode "auto" calls the backend and falls back to the in-browser mock when the backend is not reachable,
//   so the UI can be developed and demoed without running uvicorn.
import type { ErrorBody } from './types';
import { mockHandle } from './mock';
import { ApiError } from './errors';

export { ApiError };

export type ApiMode = 'auto' | 'live' | 'mock';
export type DataSource = 'live' | 'mock' | 'unknown';

export const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8000';
const ENV_MODE = ((import.meta.env.VITE_API_MODE as string | undefined) ?? 'auto') as ApiMode;
const MODE_KEY = 'thepass.apiMode';


function readMode(): ApiMode {
  try {
    const v = localStorage.getItem(MODE_KEY);
    if (v === 'auto' || v === 'live' || v === 'mock') return v;
  } catch {
    /* storage unavailable */
  }
  return ENV_MODE;
}

let mode: ApiMode = readMode();
let source: DataSource = mode === 'mock' ? 'mock' : 'unknown';
let demoUser = 'usr_owner';
/** On a public deployment: the session token from the demo passcode (see backend/app/demo_access.py). */
const SESSION_KEY = 'passtar.session';
let session: string | null = (() => { try { return localStorage.getItem(SESSION_KEY); } catch { return null; } })();
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export const apiState = {
  get mode() {
    return mode;
  },
  get source() {
    return source;
  },
  setMode(m: ApiMode) {
    mode = m;
    source = m === 'mock' ? 'mock' : 'unknown';
    try {
      localStorage.setItem(MODE_KEY, m);
    } catch {
      /* ignore */
    }
    emit();
  },
  setDemoUser(u: string) {
    demoUser = u;
  },
  get demoUser() {
    return demoUser;
  },
  get session() {
    return session;
  },
  setSession(t: string | null) {
    session = t;
    try {
      if (t) localStorage.setItem(SESSION_KEY, t);
      else localStorage.removeItem(SESSION_KEY);
    } catch {
      /* private mode */
    }
  },
  subscribe(l: () => void) {
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  },
};

function setSource(s: DataSource) {
  if (s !== source) {
    source = s;
    emit();
  }
}

export interface RequestOptions {
  body?: unknown;
  query?: Record<string, string | number | undefined>;
  headers?: Record<string, string>;
}

export async function api<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
  const query = Object.entries(opts.query ?? {}).filter(([, v]) => v !== undefined) as [string, string | number][];
  const qs = query.length ? '?' + new URLSearchParams(query.map(([k, v]) => [k, String(v)])).toString() : '';
  const headers: Record<string, string> = { 'X-Demo-User': demoUser, ...(session ? { 'X-Demo-Session': session } : {}), ...(opts.headers ?? {}) };

  const viaMock = () =>
    mockHandle(method, path, { body: opts.body, query: Object.fromEntries(query), headers }) as Promise<T>;

  if (mode === 'mock') return viaMock();

  let res: Response;
  try {
    res = await fetch(API_BASE + path + qs, {
      method,
      headers: opts.body !== undefined ? { 'Content-Type': 'application/json', ...headers } : headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    if (mode === 'auto') {
      setSource('mock');
      return viaMock();
    }
    throw new ApiError(0, 'NETWORK', `Cannot reach the backend at ${API_BASE}. Is uvicorn running?`);
  }
  setSource('live');

  if (!res.ok) {
    let body: Partial<ErrorBody> | undefined;
    try {
      body = await res.json();
    } catch {
      /* not JSON */
    }
    if (body?.error) throw new ApiError(res.status, body.error.code, body.error.message, body.error.details ?? {});
    throw new ApiError(res.status, res.status === 422 ? 'VALIDATION_ERROR' : 'HTTP_' + res.status, res.statusText || 'Request failed');
  }
  return (await res.json()) as T;
}

/** Human wording for the error codes in the technical design doc. */
export function describeError(e: unknown): string {
  if (!(e instanceof ApiError)) return e instanceof Error ? e.message : 'Something went wrong';
  const known: Record<string, string> = {
    VALIDATION_ERROR: 'Some fields are not valid. Please check the form.',
    PROTOCOL_INVALID: 'The authorization protocol is missing, inactive or expired.',
    OUT_OF_SCOPE: 'That is outside what the protocol allows.',
    RATE_LIMITED: 'Too many requests. Please wait a moment.',
    PROBE_LIMIT: 'This product was checked with too many different quantities today.',
    QUOTE_EXPIRED: 'The quote expired after 15 minutes. Choose the option again for a fresh quote.',
    QUOTE_CHANGED: 'Price or stock changed since the quote. A new quote is needed.',
    RETRY_LIMIT: 'This step has already been retried 3 times. Send it to a human instead.',
    NOT_FOUND: 'Not found.',
    UPSTREAM_ERROR: 'Valio’s price or stock service did not respond. Try again shortly.',
    SELF_APPROVAL_FORBIDDEN: 'You requested this order, so someone else has to approve it.',
    NOT_APPROVER: 'Only the approver named in the protocol can decide.',
    AUTHORIZATION_INVALID: 'The approval credential is not valid.',
  };
  return known[e.code] ?? e.message;
}
