// In-browser stand-in for the FastAPI fake server. Same routes, same shapes, same error codes.
// Unlike the stateless fixture server it remembers what happened (selected quote, orders, approval decisions,
// faults, audit entries), so the multi-role demo stays coherent: chef submits → manager approves → log grows.
import { ApiError } from '../errors';
import type {
  ApprovalCard, ApprovalDecision, AuditEntry, Candidate, CreateRequest, GateDecision, Order, ProfileBody,
  Protocol, Quote, RejectNodeRequest, RequestResult,
} from '../types';
import { draftFrom, ROGUE_AUDIT, seed, selfProtocol } from './data';

interface Ctx {
  body?: unknown;
  query: Record<string, string | number>;
  headers: Record<string, string>;
}

let db = fresh();

function fresh() {
  const s = seed();
  return {
    ...s,
    protocols: { [s.protocol.id]: s.protocol, prot_bistro_v1: selfProtocol(s.protocol) } as Record<string, Protocol>,
    quotes: {} as Record<string, Quote>,
    orders: {} as Record<string, Order>,
    idempotency: {} as Record<string, string>,
    retries: { 1: 3, 2: 3, 3: 3, 4: 3 } as Record<number, number>,
    requester: 'usr_chef',
    seq: 0,
  };
}

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));
const nowIso = () => new Date().toISOString();
const err = (status: number, code: string, message: string, details: Record<string, unknown> = {}) =>
  new ApiError(status, code, message, details);

async function sha256(text: string): Promise<string> {
  try {
    const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
    return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, '0')).join('');
  } catch {
    let h = 0;
    for (let i = 0; i < text.length; i++) h = (Math.imul(31, h) + text.charCodeAt(i)) | 0;
    return (h >>> 0).toString(16).padStart(64, '0');
  }
}

/** Append-only, hash-chained: hash = sha256(prev_hash + at + actor + event + payload_json). */
async function audit(actor: string, event: string, summary: string, disclosed: AuditEntry['disclosed_fields'] = []) {
  const prev = db.audit[db.audit.length - 1];
  const at = nowIso();
  const payload = {};
  const hash = await sha256(prev.hash + at + actor + event + JSON.stringify(payload));
  db.audit.push({ seq: prev.seq + 1, at, request_id: 'req_1', actor, event, summary, disclosed_fields: disclosed, payload, prev_hash: prev.hash, hash });
}

const userLabel = (u: string) => `user:${u}`;
const gross = (c: { totals?: { gross_cents: number } | null }) => c.totals?.gross_cents ?? 0;

function gateFor(c: Candidate, limit: number): GateDecision {
  if (c.outcome_preview === 'AUTO')
    return { outcome: 'AUTO', rules_hit: [{ rule_id: 'R-SPEND-AUTO', description: 'Total ≤ auto-approve limit', outcome: 'AUTO' }], summary: 'Within the auto-approve limit; all hard requirements explicitly declared' };
  return {
    outcome: c.outcome_preview,
    rules_hit: [{ rule_id: 'R-SPEND-APPROVAL', description: 'Auto-approve limit < total ≤ order maximum', outcome: 'BUYER_APPROVAL' }],
    summary: `Above the €${(limit / 100).toLocaleString('en')} auto-approve limit; needs approval`,
  };
}

function allCandidates(): Candidate[] {
  return [...db.request.candidates, ...(db.request.blocked ?? [])];
}

/** The fixture approval is written for the hotel (chef → manager). Reshape it for whoever actually requested. */
function buyerApprovalFor(card: ApprovalCard): ApprovalCard {
  const self = db.requester === 'usr_owner';
  return self ? { ...card, requested_by: 'usr_owner', approver: 'usr_owner', approval_mode: 'SELF' } : card;
}

type Handler = (m: RegExpMatchArray, ctx: Ctx) => unknown | Promise<unknown>;
const routes: [string, RegExp, Handler][] = [
  // ── protocols ──
  ['POST', /^\/v1\/protocols\/draft$/, (_m, { body }) => {
    const d = draftFrom(db.protocol, (body as { description: string }).description);
    db.protocols[d.id] = d;
    return d;
  }],
  ['GET', /^\/v1\/protocols\/([^/]+)$/, ([, id]) => db.protocols[id] ?? db.protocol],
  ['GET', /^\/v1\/agents\/([^/]+)\/protocol$/, ([, agentId]) =>
    Object.values(db.protocols).filter((p) => p.body.agent_id === agentId && p.status === 'ACTIVE')
      .sort((a, b) => b.version - a.version)[0] ?? db.protocol],
  ['POST', /^\/v1\/protocols\/([^/]+)\/sign$/, async ([, id], { headers }) => {
    const p = db.protocols[id] ?? db.protocol;
    db.protocols[id] = { ...p, status: 'PENDING_VALIO_CONFIRMATION', buyer_signed_at: nowIso() };
    await audit(userLabel(headers['X-Demo-User']), 'PROTOCOL_SIGNED', `Protocol ${id} signed by the buyer; waiting for Valio`);
    return db.protocols[id];
  }],
  ['POST', /^\/v1\/protocols\/([^/]+)\/confirm$/, async ([, id]) => {
    const p = db.protocols[id] ?? db.protocol;
    db.protocols[id] = { ...p, status: 'ACTIVE', valio_confirmed_at: nowIso(), jws: 'eyJhbGciOiJFZERTQSIsImtpZCI6InZhbGlvLTEifQ.mock.mock' };
    await audit('user:usr_valio', 'PROTOCOL_CONFIRMED', `Valio confirmed ${id}; protocol is active`);
    return db.protocols[id];
  }],
  ['POST', /^\/v1\/protocols\/([^/]+)\/amend$/, async () => {
    await audit('user:usr_manager', 'PROTOCOL_AMENDED', 'Tightening applied; expansion waits for signature; forbidden change rejected');
    return db.protocolDiff;
  }],
  // ── profiles ──
  ['GET', /^\/v1\/buyers\/([^/]+)\/profiles$/, () => db.profiles],
  ['PUT', /^\/v1\/profiles\/([^/]+)$/, async ([, id], { body }) => {
    const i = Math.max(0, db.profiles.findIndex((p) => p.id === id));
    db.profiles[i] = { ...db.profiles[i], body: body as ProfileBody, updated_at: nowIso() };
    await audit('user:buyer', 'PROFILE_UPDATED', `Profile “${(body as ProfileBody).name}” updated`);
    return db.profiles[i];
  }],
  // ── catalog ──
  ['GET', /^\/v1\/catalog\/facets$/, () => db.facets],
  // ── requests ──
  ['POST', /^\/v1\/requests\/parse$/, () => db.parse],
  ['POST', /^\/v1\/requests$/, async (_m, { body, headers }) => {
    const b = body as CreateRequest;
    if (db.faults.find((f) => f.name === 'TIMEOUT')?.enabled) throw err(502, 'UPSTREAM_ERROR', 'Valio API timed out');
    if (db.faults.find((f) => f.name === 'RATE_LIMIT')?.enabled) throw err(429, 'RATE_LIMITED', 'Too many requests');
    const fresh = seed().request;
    db.retries = { 1: 3, 2: 3, 3: 3, 4: 3 };
    db.requester = headers['X-Demo-User'] ?? 'usr_chef';
    db.request = { ...fresh, structured: { ...fresh.structured, ...b.structured } };
    if (db.faults.find((f) => f.name === 'EMPTY_RESULT')?.enabled)
      db.request = { ...db.request, status: 'NO_CANDIDATES', candidates: [], no_candidate_reason: 'No product meets every hard requirement for that date.' };
    await audit('agent:agt_hotel', 'REQUEST_CREATED', `Request received (${b.input_mode === 'PANEL' ? 'panel' : 'natural language'})`);
    await audit('system', 'CANDIDATES_DISCLOSED', `Disclosed prices and deliverability (L1) for ${db.request.candidates.length} candidates`,
      [{ field: 'price.net', sensitivity: 'L1' }, { field: 'availability.deliverable', sensitivity: 'L1' }]);
    return db.request;
  }],
  ['GET', /^\/v1\/requests\/([^/]+)$/, () => db.request],
  ['POST', /^\/v1\/requests\/([^/]+)\/nodes\/(\d)\/reject$/, async ([, , n], { body }) => {
    const node = Number(n);
    const b = body as RejectNodeRequest;
    if (node === 4 && !['SEND_TO_APPROVAL', 'PICK_OTHER'].includes(b.reason))
      throw err(422, 'VALIDATION_ERROR', 'The protocol check can only be sent to approval or answered by picking another option');
    if (db.retries[node] <= 0) throw err(409, 'RETRY_LIMIT', 'This step has been retried 3 times');
    db.retries[node] -= 1;
    const attempt = db.request.attempt + 1;
    let candidates = db.request.candidates;
    if (b.reason === 'EXCLUDE_OPTION' && b.params?.candidate_id) candidates = candidates.filter((c) => c.id !== b.params?.candidate_id);
    if (b.reason === 'EXCLUDE_PRODUCT' && b.params?.product_id) candidates = candidates.filter((c) => c.lines[0].product_id !== b.params?.product_id);
    let structured = db.request.structured;
    if (node === 1 && b.params?.structured) structured = b.params.structured as RequestResult['structured'];
    db.request = {
      ...db.request,
      attempt,
      structured,
      candidates: candidates.map((c, i) => ({ ...c, rank: i + 1 })),
      chain: db.request.chain.map((c) => ({
        ...c,
        attempt: c.node >= node ? attempt : c.attempt,
        retries_left: c.node === node ? db.retries[node] : c.retries_left,
        summary: c.node === node ? `${c.summary.replace(/ \(regenerated.*\)$/, '')} (regenerated: ${b.reason.toLowerCase().replace(/_/g, ' ')})` : c.summary,
      })),
    };
    await audit('user:buyer', 'NODE_REJECTED', `Step ${node} marked “not right” (${b.reason}); regenerated from step ${node}`);
    return db.request;
  }],
  ['GET', /^\/v1\/requests\/([^/]+)\/chain$/, () => ({ ...db.chain, attempt: db.request.attempt })],
  ['POST', /^\/v1\/requests\/([^/]+)\/select$/, async ([, rid], { body }) => {
    const id = (body as { candidate_id: string }).candidate_id;
    const c = allCandidates().find((x) => x.id === id);
    if (!c) throw err(404, 'NOT_FOUND', `Candidate ${id} not found`);
    if (!c.selectable || !c.totals) throw err(403, 'OUT_OF_SCOPE', 'Blocked options cannot be selected', { violations: c.violations });
    const proto = db.protocols[db.requester === 'usr_owner' ? 'prot_bistro_v1' : db.protocol.id];
    const quote: Quote = {
      id: 'quo_' + id.replace('cand_', ''), request_id: rid, candidate_id: id, protocol_id: proto.id, lines: c.lines, totals: c.totals,
      delivery_date: c.delivery_date, valid_until: new Date(Date.now() + 15 * 60_000).toISOString(), status: 'VALID', jws: db.select.quote.jws,
    };
    db.quotes[quote.id] = quote;
    await audit('system', 'QUOTE_SIGNED', `Signed quote ${quote.id} for ${id}, valid 15 minutes`);
    return { quote, gate: gateFor(c, proto.body.auto_approve_limit_cents) };
  }],
  // ── quotes & orders ──
  ['GET', /^\/v1\/quotes\/([^/]+)\/verify$/, ([, id]) => {
    const q = db.quotes[id];
    return { quote_id: id, signature_valid: true, expired: q ? Date.parse(q.valid_until) < Date.now() : false, key_id: 'valio-1' };
  }],
  ['GET', /^\/v1\/quotes\/([^/]+)$/, ([, id]) => db.quotes[id] ?? db.select.quote],
  ['POST', /^\/v1\/orders$/, async (_m, { body, headers }) => {
    const key = headers['Idempotency-Key'];
    if (!key) throw err(422, 'VALIDATION_ERROR', 'Idempotency-Key header is required');
    if (db.idempotency[key]) return db.orders[db.idempotency[key]];
    const { quote_id } = body as { quote_id: string };
    const q = db.quotes[quote_id];
    if (!q) throw err(404, 'NOT_FOUND', `Quote ${quote_id} not found`);
    if (Date.parse(q.valid_until) < Date.now()) throw err(409, 'QUOTE_EXPIRED', 'Quote is older than 15 minutes');
    const c = allCandidates().find((x) => x.id === q.candidate_id)!;
    const gate = gateFor(c, db.protocols[q.protocol_id]?.body.auto_approve_limit_cents ?? 100000);
    const auto = gate.outcome === 'AUTO';
    const order: Order = {
      id: 'ord_' + (Object.keys(db.orders).length + 1), request_id: q.request_id, quote_id, gate,
      status: auto ? 'PLACED' : 'PENDING_BUYER_APPROVAL', approval_id: auto ? null : 'apr_1',
      created_at: nowIso(), placed_at: auto ? nowIso() : null,
    };
    if (!auto) {
      const card = db.approvals.find((a) => a.id === 'apr_1')!;
      Object.assign(card, buyerApprovalFor({ ...card, status: 'PENDING' }));
      if (c.totals) {
        card.title = `1,000 L lactose-free milk, €${(gross(c) / 100).toLocaleString('en', { minimumFractionDigits: 2 })}`;
        card.candidate = c;
        card.quote = q;
      }
    }
    db.orders[order.id] = order;
    db.idempotency[key] = order.id;
    await audit(userLabel(headers['X-Demo-User']), auto ? 'ORDER_PLACED' : 'APPROVAL_REQUESTED',
      auto ? `Rule R-SPEND-AUTO passed; order ${order.id} placed` : `Rule R-SPEND-APPROVAL hit; approval apr_1 created`);
    return order;
  }],
  ['GET', /^\/v1\/orders\/([^/]+)$/, ([, id]) => db.orders[id] ?? db.order],
  // ── approvals ──
  ['GET', /^\/v1\/approvals$/, (_m, { query }) =>
    db.approvals
      .filter((a) => !query.kind || a.kind === query.kind)
      .filter((a) => !query.status || a.status === query.status)
      .map((a) => (a.kind === 'BUYER' ? buyerApprovalFor(a) : a))],
  ['GET', /^\/v1\/approvals\/([^/]+)$/, ([, id]) => db.approvals.find((a) => a.id === id) ?? db.approvals[0]],
  ['POST', /^\/v1\/approvals\/([^/]+)\/decision$/, async ([, id], { body, headers }) => {
    const raw = db.approvals.find((a) => a.id === id);
    if (!raw) {
      // e.g. the protocol-change approval apr_prot_1 that only exists as an id in protocol_diff.json
      return { approval: { ...db.approvals[0], id, status: (body as ApprovalDecision).decision === 'REJECT' ? 'REJECTED' : 'APPROVED' }, order: null };
    }
    const a = raw.kind === 'BUYER' ? buyerApprovalFor(raw) : raw;
    const user = headers['X-Demo-User'];
    const d = body as ApprovalDecision;
    if (a.kind === 'BUYER' && user) {
      if (a.approval_mode === 'SEPARATE' && user === a.requested_by)
        throw err(403, 'SELF_APPROVAL_FORBIDDEN', 'In SEPARATE mode the requester cannot approve their own order', { requested_by: a.requested_by, approver: a.approver });
      if (user !== a.approver)
        throw err(403, 'NOT_APPROVER', 'Only the approver named in the protocol can decide', { requested_by: a.requested_by, approver: a.approver });
    }
    const status: ApprovalCard['status'] = d.decision === 'REJECT' ? 'REJECTED' : d.decision === 'ROUTE' ? 'ROUTED' : d.decision === 'APPROVE_WITH_CHANGES' ? 'APPROVED_WITH_CHANGES' : 'APPROVED';
    raw.status = status;
    let order: Order | null = null;
    if (a.kind === 'BUYER' && status.startsWith('APPROVED')) {
      const existing = Object.values(db.orders).find((o) => o.approval_id === a.id);
      const base: Order = existing ?? { ...db.order, id: 'ord_3', quote_id: a.quote?.id ?? 'quo_3', approval_id: a.id };
      const changed = db.faults.find((f) => f.name === 'PRICE_CHANGE')?.enabled;
      order = changed
        ? { ...base, status: 'REQUOTE_REQUIRED', requote_reason: 'Price went up 8% since the quote; the approved quote is no longer valid', new_quote_id: 'quo_3b', placed_at: null }
        : { ...base, status: 'PLACED', placed_at: nowIso() };
      db.orders[order.id] = order;
      const self = a.approval_mode === 'SELF' && user === a.requested_by;
      await audit(userLabel(user), changed ? 'REQUOTE_REQUIRED' : self ? 'ORDER_SELF_APPROVED' : 'ORDER_APPROVED',
        changed ? 'Approved, but the re-check found a price change; new quote issued' : `Approved${self ? ' (self-approved)' : ''}; re-check passed; order ${order.id} placed`);
    } else {
      await audit(userLabel(user), 'APPROVAL_' + status, `${a.id} ${status.toLowerCase().replace(/_/g, ' ')}${d.note ? `: ${d.note}` : ''}`);
    }
    return { approval: { ...a, status }, order };
  }],
  // ── audit ──
  ['GET', /^\/v1\/audit$/, (_m, { headers }) => {
    const valio = headers['X-Demo-User'] === 'usr_valio';
    if (!valio) return db.audit;
    const last = db.audit[db.audit.length - 1];
    return [...db.audit, { ...ROGUE_AUDIT, seq: last.seq + 1, request_id: null, disclosed_fields: [], payload: {}, prev_hash: last.hash, hash: 'd4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4' }];
  }],
  ['GET', /^\/v1\/audit\/verify$/, async () => {
    // fixture entries carry placeholder hashes, so only verify the links (prev_hash) plus our own computed hashes
    for (let i = 1; i < db.audit.length; i++) {
      if (db.audit[i].prev_hash !== db.audit[i - 1].hash) return { ok: false, entries_checked: i + 1, first_bad_seq: db.audit[i].seq };
    }
    return { ok: true, entries_checked: db.audit.length, first_bad_seq: null };
  }],
  // ── admin ──
  ['GET', /^\/v1\/admin\/faults$/, () => db.faults],
  ['PUT', /^\/v1\/admin\/faults\/([A-Z_]+)$/, ([, name], { body }) => {
    const f = db.faults.find((x) => x.name === name);
    if (!f) throw err(404, 'NOT_FOUND', `Unknown fault ${name}`);
    f.enabled = (body as { enabled: boolean }).enabled;
    return f;
  }],
  ['POST', /^\/v1\/admin\/reset$/, () => {
    db = fresh();
    return { ok: true };
  }],
];

export async function mockHandle(method: string, path: string, ctx: Ctx): Promise<unknown> {
  await delay(180 + Math.random() * 220);
  for (const [m, re, handler] of routes) {
    if (m !== method) continue;
    const match = path.match(re);
    if (match) return structuredClone(await handler(match, ctx));
  }
  throw err(404, 'NOT_FOUND', `No mock for ${method} ${path}`);
}
