// Seed data for the in-browser mock. Imported straight from the repo's fixtures/ so it stays in sync
// with what the FastAPI fake server returns. Only things the fixtures lack are defined here.
import approvalsJson from '../../../../fixtures/approvals.json';
import auditJson from '../../../../fixtures/audit.json';
import chainJson from '../../../../fixtures/chain_timeline.json';
import facetsJson from '../../../../fixtures/facets.json';
import faultsJson from '../../../../fixtures/faults.json';
import orderJson from '../../../../fixtures/order.json';
import parseJson from '../../../../fixtures/parse_result.json';
import profilesJson from '../../../../fixtures/profiles.json';
import protocolJson from '../../../../fixtures/protocol.json';
import protocolDiffJson from '../../../../fixtures/protocol_diff.json';
import requestJson from '../../../../fixtures/request_result.json';
import selectJson from '../../../../fixtures/select_result.json';
import type {
  ApprovalCard, AuditEntry, Candidate, ChainTimeline, Facets, Fault, Order, ParseResult, Profile, Protocol,
  ProtocolDiff, RequestResult, SelectResult,
} from '../types';

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));

export const seed = () => ({
  approvals: clone(approvalsJson) as unknown as ApprovalCard[],
  audit: clone(auditJson) as unknown as AuditEntry[],
  chain: clone(chainJson) as unknown as ChainTimeline,
  facets: clone(facetsJson) as unknown as Facets,
  faults: clone(faultsJson) as unknown as Fault[],
  order: clone(orderJson) as unknown as Order,
  parse: clone(parseJson) as unknown as ParseResult,
  profiles: clone(profilesJson) as unknown as Profile[],
  protocol: clone(protocolJson) as unknown as Protocol,
  protocolDiff: clone(protocolDiffJson) as unknown as ProtocolDiff,
  request: withExtraCandidates(clone(requestJson) as unknown as RequestResult),
  select: clone(selectJson) as unknown as SelectResult,
});

/** Mock-only: two lower-ranked options so "show more options" has something to show (the fixture has 3). */
function withExtraCandidates(r: RequestResult): RequestResult {
  const base = r.candidates.find((c) => c.id === 'cand_2') ?? r.candidates[0];
  const make = (id: string, rank: number, productId: string, name: string, units: number, unitPrice: number,
    reliability: Candidate['reliability_level'], fulfilment: 'MEDIUM' | 'LOW', explanation: string): Candidate => {
    const c = clone(base);
    const net = units * unitPrice;
    const vat = Math.round(net * 0.135);
    c.id = id;
    c.rank = rank;
    c.badge = null;
    c.lines = [{ ...c.lines[0], product_id: productId, name, sales_units: units, unit_price_cents: unitPrice, line_net_cents: net }];
    c.totals = { net_cents: net, vat_cents: vat, deposit_cents: 0, gross_cents: net + vat };
    c.reliability_level = reliability;
    c.outcome_preview = 'BUYER_APPROVAL';
    c.outcome_reasons = [`€${((net + vat) / 100).toFixed(2)}, above the auto-approve limit of €1,000`];
    c.cards.trust = { evidence: 'HIGH', match: 'HIGH', fulfilment, compliance: 'MEDIUM', overall: fulfilment, how_to_improve: 'Above the auto-approve limit; needs approval' };
    c.cards.allergen.rows = c.cards.allergen.rows.map((row) => ({ ...row, product_id: productId }));
    c.explanation = explanation;
    return c;
  };
  return {
    ...r,
    candidates: [
      ...r.candidates,
      make('cand_4', 4, '464440', 'Valio semi skimmed milk 0,5 l lactose free UHT', 2000, 45, 'LOW', 'LOW',
        'Shelf-stable, but 2,000 half-litre cartons and a weak delivery record.'),
      make('cand_5', 5, '465039', 'Valio Eila® semi skimmed milk drink 1 l lactose free', 1000, 92, 'MEDIUM', 'MEDIUM',
        'Same product family as the cheapest option, slightly pricier and less reliable.'),
    ],
  };
}

/** The fixtures only have the SEPARATE hotel protocol; the single-owner café needs a SELF one. */
export function selfProtocol(p: Protocol): Protocol {
  return { ...clone(p), id: 'prot_bistro_v1', body: { ...clone(p.body), buyer_id: 'buy_bistro', agent_id: 'agt_bistro', approval_mode: 'SELF', approver: 'usr_owner' } };
}

export function draftFrom(p: Protocol, description: string): Protocol {
  return {
    ...clone(p),
    id: 'prot_draft_1',
    status: 'DRAFT',
    version: (p.version ?? 1) + 1,
    jws: null,
    supersedes: p.id,
    buyer_signed_at: null,
    valio_confirmed_at: null,
    created_at: new Date().toISOString(),
    open_questions: [
      { field: 'hard_requirements', question: `You said: “${description.slice(0, 80)}”. Should nuts be excluded as well?`, suggested: 'ALLERGEN_FREE:AN' },
      { field: 'auto_approve_limit_cents', question: 'Auto-approve up to €1,000 per order?', suggested: 100000 },
    ],
  };
}

/** Audit entry the mock server would have seen (shown only to Valio). */
export const ROGUE_AUDIT = {
  at: '2026-11-14T10:04:12+02:00',
  actor: 'agent:agt_rogue',
  event: 'REQUEST_BLOCKED',
  summary: 'Blocked: asked for exact stock (L2) of 463038; probe limit reached',
};

/** No agents endpoint exists yet — shown on the Valio "Agents" page with a note. */
export const AGENTS = [
  { id: 'agt_hotel', name: 'Hotel Helsinki Central buyer agent', requests: 38, blocked: 0 },
  { id: 'agt_hospital', name: 'Hospital kitchen buyer agent', requests: 12, blocked: 1 },
  { id: 'agt_rogue', name: 'Unknown Procurement Bot', requests: 212, blocked: 47 },
];
