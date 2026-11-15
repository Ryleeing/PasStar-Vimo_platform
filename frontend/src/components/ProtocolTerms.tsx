// Every term of a protocol in plain words: what Valio reads before confirming, and what was confirmed.
import type { Facets, Protocol } from '../api/types';
import { ACCOUNTS, WORKSPACES } from '../config/accounts';
import { eur } from '../lib/format';

export const buyerName = (buyerId: string) => Object.values(WORKSPACES).find((w) => w.buyerId === buyerId)?.name ?? buyerId;
const person = (userId: string) => {
  const a = ACCOUNTS.find((x) => x.userId === userId);
  return a ? `${a.name}, ${a.title.toLowerCase()}` : userId;
};
const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—');

export function ProtocolTerms({ p, facets }: { p: Protocol; facets?: Facets | null }) {
  const b = p.body;
  const category = (code: string) => facets?.categories.find((c) => c.code === code)?.label ?? code;
  const s = b.valio_scope;
  return (
    <div className="kv small terms">
      <div className="k">Buyer</div><div>{buyerName(b.buyer_id)}</div>
      <div className="k">Agent</div><div className="mono">{b.agent_id}</div>
      <div className="k">May buy</div><div>{b.allowed_categories.map(category).join(', ')}</div>
      <div className="k">Always</div><div>{(b.hard_requirements ?? []).map((r) => r.label ?? r.code).join(', ') || 'No hard requirements'}</div>
      <div className="k">Orders alone</div><div>up to {eur(b.auto_approve_limit_cents)}</div>
      <div className="k">Needs approval</div><div>{eur(b.auto_approve_limit_cents)}–{eur(b.max_order_cents)}, by {person(b.approver)}{b.approval_mode === 'SELF' ? ' (approves own orders, logged separately)' : ' (never the requester)'}{b.approval_channel === 'EXTERNAL' ? ' in the buyer’s own system' : ' in PasStar'}</div>
      <div className="k">Never</div><div>above {eur(b.max_order_cents)}</div>
      <div className="k">Unknown data</div><div>{b.unknown_data_policy === 'REJECT' ? 'refused' : 'needs approval'}; “may contain” allergens {b.may_contain_policy === 'EXCLUDE' ? 'excluded' : 'need approval'}</div>
      <div className="k">Agent may see</div><div>{s.prices === 'OWN_ONLY' ? 'its own prices only' : s.prices}; stock as {s.stock === 'DELIVERABLE_YES_NO' ? 'deliverable yes/no' : s.stock}; data up to {s.sensitivity_ceiling}</div>
      <div className="k">Agent limits</div><div>{s.rate_limit_per_min} calls a minute; {s.max_probes_per_product_per_day} different quantities per product a day</div>
      <div className="k">Valid until</div><div>{b.valid_until}</div>
      <div className="k">Signed</div><div>by the buyer {when(p.buyer_signed_at)}{p.valio_confirmed_at ? <> · confirmed by Valio {when(p.valio_confirmed_at)}</> : null}</div>
      {p.supersedes && <><div className="k">Replaces</div><div className="mono">{p.supersedes}</div></>}
    </div>
  );
}
