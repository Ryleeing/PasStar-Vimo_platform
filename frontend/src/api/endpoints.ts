// One function per backend route (see backend/app/main.py and the design doc §4).
import { api } from './client';
import type {
  AgentConnection, AgentKind, ApprovalCard, ApprovalDecision, ApprovalDecisionResult, AuditEntry, AuditVerifyResult, ChainTimeline,
  CreateRequest, Facets, Fault, FaultName, Order, ParseResult, Profile, ProfileBody, Protocol, ProtocolBody,
  IssuedKey, OrderSummary, ProtocolDiff, Quote, QuoteVerifyResult, RejectNodeRequest, RequestResult, SelectResult,
} from './types';

const enc = encodeURIComponent;

// ── Protocols (BE1) ──
export const draftProtocol = (buyer_id: string, agent_id: string, description: string) =>
  api<Protocol>('POST', '/v1/protocols/draft', { body: { buyer_id, agent_id, description } });
export const getProtocol = (id: string) => api<Protocol>('GET', `/v1/protocols/${enc(id)}`);
/** The protocol the workspace's agent acts under now. Never keep a protocol id: raising a limit makes a new version. */
/** Valio: every buyer's protocols; a buyer: its own. PENDING_VALIO_CONFIRMATION is Valio's to-do list. */
export const listProtocols = (status?: string) =>
  api<Protocol[]>('GET', '/v1/protocols', { query: { status } });
export const getActiveProtocol = (agentId: string) => api<Protocol>('GET', `/v1/agents/${enc(agentId)}/protocol`);
export const updateProtocol = (id: string, body: ProtocolBody) =>
  api<Protocol>('PUT', `/v1/protocols/${enc(id)}`, { body: { body } });
export const signProtocol = (id: string) => api<Protocol>('POST', `/v1/protocols/${enc(id)}/sign`);
export const confirmProtocol = (id: string) => api<Protocol>('POST', `/v1/protocols/${enc(id)}/confirm`);
export const amendProtocol = (id: string, changes: Record<string, unknown>) =>
  api<ProtocolDiff>('POST', `/v1/protocols/${enc(id)}/amend`, { body: { changes } });

// ── Profiles (BE3) ──
export const listProfiles = (buyerId: string) => api<Profile[]>('GET', `/v1/buyers/${enc(buyerId)}/profiles`);
export const updateProfile = (id: string, body: ProfileBody) => api<Profile>('PUT', `/v1/profiles/${enc(id)}`, { body });

// ── Catalog (BE2) ──
export const getFacets = () => api<Facets>('GET', '/v1/catalog/facets');

// ── Requests, candidates, chain (BE3) ──
export const parseRequest = (text: string, profile_id?: string) =>
  api<ParseResult>('POST', '/v1/requests/parse', { body: { text, profile_id } });
export const createRequest = (body: CreateRequest) => api<RequestResult>('POST', '/v1/requests', { body });
export const getRequest = (id: string) => api<RequestResult>('GET', `/v1/requests/${enc(id)}`);
export const rejectNode = (id: string, node: number, body: RejectNodeRequest) =>
  api<RequestResult>('POST', `/v1/requests/${enc(id)}/nodes/${node}/reject`, { body });
export const getChain = (id: string, attempt?: number) =>
  api<ChainTimeline>('GET', `/v1/requests/${enc(id)}/chain`, { query: { attempt } });
export const selectCandidate = (id: string, candidate_id: string) =>
  api<SelectResult>('POST', `/v1/requests/${enc(id)}/select`, { body: { candidate_id } });

// ── Quotes & orders (BE1) ──
export const verifyQuote = (id: string) => api<QuoteVerifyResult>('GET', `/v1/quotes/${enc(id)}/verify`);
export const getQuote = (id: string) => api<Quote>('GET', `/v1/quotes/${enc(id)}`);
export const placeOrder = (quote_id: string, protocol_id: string, idempotencyKey: string) =>
  api<Order>('POST', '/v1/orders', { body: { quote_id, protocol_id }, headers: { 'Idempotency-Key': idempotencyKey } });
export const getOrder = (id: string) => api<Order>('GET', `/v1/orders/${enc(id)}`);

// ── Approvals (BE1) ──
export const listApprovals = (kind: 'BUYER' | 'VALIO', status?: ApprovalCard['status']) =>
  api<ApprovalCard[]>('GET', '/v1/approvals', { query: { kind, status } })
    // the M1 fixture server ignores the filters, so filter again here
    .then((xs) => xs.filter((a) => a.kind === kind && (!status || a.status === status)));
export const decideApproval = (id: string, body: ApprovalDecision) =>
  api<ApprovalDecisionResult>('POST', `/v1/approvals/${enc(id)}/decision`, { body });

// ── Audit & admin ──
export const getAudit = (request_id?: string) => api<AuditEntry[]>('GET', '/v1/audit', { query: { request_id } });
export const verifyAudit = () => api<AuditVerifyResult>('GET', '/v1/audit/verify');
export const listFaults = () => api<Fault[]>('GET', '/v1/admin/faults');
export const setFault = (name: FaultName, enabled: boolean) =>
  api<Fault>('PUT', `/v1/admin/faults/${name}`, { body: { enabled, params: {} } });
export const resetDemo = () => api<{ ok: boolean }>('POST', '/v1/admin/reset');

// ── External agents and their keys ──
export const listAgents = () => api<AgentConnection[]>('GET', '/v1/agents');
export const issueAgentKey = (agentId: string, kind: AgentKind) =>
  api<IssuedKey>('POST', `/v1/agents/${enc(agentId)}/key`, { body: { kind } });
export const revokeAgentKey = (agentId: string) => api<AgentConnection>('POST', `/v1/agents/${enc(agentId)}/revoke`);
/** Valio only: a blocked agent is refused even with a valid key. */
export const blockAgent = (agentId: string, blocked: boolean) =>
  api<AgentConnection>('POST', `/v1/agents/${enc(agentId)}/${blocked ? 'block' : 'unblock'}`);
/** Calls PasStar the way the agent itself would: with its key. Used to test a fresh connection. */
export const protocolAsAgent = (agentId: string, key: string) =>
  api<Protocol>('GET', `/v1/agents/${enc(agentId)}/protocol`, { headers: { 'X-Agent-Key': key } });

/** Every order of the signed-in person's buyer, newest first, whoever placed it (a person or an agent). */
export const listOrders = () => api<OrderSummary[]>('GET', '/v1/orders');
