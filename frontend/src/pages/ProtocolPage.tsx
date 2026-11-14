import { useState } from 'react';
import { BadgeCheck, FilePlus2, FileSignature, HelpCircle, ShieldAlert, ShieldCheck, ShieldPlus, Sparkles } from 'lucide-react';
import { amendProtocol, decideApproval, draftProtocol, getProtocol, signProtocol } from '../api/endpoints';
import { describeError } from '../api/client';
import type { Protocol, ProtocolBody, ProtocolDiff } from '../api/types';
import { displayUser } from '../config/accounts';
import { eur, POLICY, PROTOCOL_STATUS } from '../lib/format';
import { useSession } from '../state/session';
import { useFlow } from '../state/flow';
import { Banner, Button, ErrorState, PageHead, Pill, Skeleton, useAsync, useToast } from '../components/ui';

const CATEGORY: Record<string, string> = { '10400': 'Milk', '10401': 'Cream', '10405': 'Dairy drinks', '10430': 'Quark', '22540': 'Dry goods' };
const cat = (c: string) => (CATEGORY[c] ? `${CATEGORY[c]} (${c})` : c);
const fmtVal = (field: string, v: unknown): string => {
  if (field.endsWith('_cents') && typeof v === 'number') return eur(v);
  if (field === 'allowed_categories' && Array.isArray(v)) return v.map((x) => CATEGORY[x] ?? x).join(', ');
  return typeof v === 'string' ? v : JSON.stringify(v);
};

export function ProtocolBodyView({ b }: { b: ProtocolBody }) {
  const sub = b.substitution;
  const scope = b.valio_scope;
  const Section = ({ title, children }: { title: string; children: React.ReactNode }) => (
    <section className="stack sm">
      <div className="side-label" style={{ padding: 0, margin: 0 }}>{title}</div>
      <div className="kv small">{children}</div>
    </section>
  );
  return (
    <div className="stack">
      <Section title="Spending">
        <div className="k">Auto-order up to</div><div className="strong">{eur(b.auto_approve_limit_cents)} <span className="muted">incl. VAT</span></div>
        <div className="k">Hard maximum</div><div>{eur(b.max_order_cents)} <span className="muted">· above is rejected</span></div>
      </Section>
      <Section title="What may be bought">
        <div className="k">Categories</div><div>{b.allowed_categories.map(cat).join(', ')}</div>
        <div className="k">Every order must be</div><div>{(b.hard_requirements ?? []).map((r) => r.label ?? r.code).join(', ') || '—'}</div>
        <div className="k">Data unknown</div><div>{POLICY[b.unknown_data_policy ?? 'REQUIRE_APPROVAL']}</div>
        <div className="k">“May contain”</div><div>{POLICY[b.may_contain_policy ?? 'EXCLUDE']}</div>
        <div className="k">Substitutes</div><div>Official {POLICY[sub?.official ?? 'AUTO'].toLowerCase()} · same category {POLICY[sub?.same_category ?? 'APPROVAL'].toLowerCase()} · cross category {POLICY[sub?.cross_category ?? 'APPROVAL'].toLowerCase()}</div>
      </Section>
      <Section title="Approvals">
        <div className="k">Mode</div><div>{b.approval_mode === 'SELF' ? <><b>SELF</b> · owner may approve own orders, logged as self-approved</> : <><b>SEPARATE</b> · requester cannot approve own orders</>}</div>
        <div className="k">Approver</div><div>{displayUser(b.approver)}</div>
        <div className="k">Where</div><div>{b.approval_channel === 'EXTERNAL' ? 'Buyer’s own system (signed credential)' : 'The Pass approval page'}</div>
        <div className="k">Valid until</div><div>{b.valid_until}</div>
      </Section>
      <Section title="What the agent may see (set by Valio)">
        <div className="k">Prices</div><div>Own prices only</div>
        <div className="k">Stock</div><div>Deliverable yes / no, never quantities</div>
        <div className="k">Rate limit</div><div>{scope?.rate_limit_per_min ?? 30} / min · {scope?.max_probes_per_product_per_day ?? 3} quantity probes per product per day</div>
        <div className="k">Sensitivity ceiling</div><div>{scope?.sensitivity_ceiling ?? 'L1'}</div>
      </Section>
    </div>
  );
}

export function DiffList({ d }: { d: ProtocolDiff }) {
  const groups: [keyof Pick<ProtocolDiff, 'tightened' | 'expanded' | 'forbidden'>, 'ok' | 'warn' | 'bad', string, React.ReactNode][] = [
    ['tightened', 'ok', 'Tightened · applied at once', <ShieldCheck key="i" size={16} />],
    ['expanded', 'warn', 'Expanded · needs the approver’s signature', <ShieldPlus key="i" size={16} />],
    ['forbidden', 'bad', 'Not allowed · rejected', <ShieldAlert key="i" size={16} />],
  ];
  return (
    <div className="stack sm">
      {groups.map(([k, tone, label, icon]) =>
        (d[k] ?? []).map((c) => (
          <div key={k + c.field} className="diff-row">
            <div className={`icon-tile ${tone}`} style={{ width: 30, height: 30 }}>{icon}</div>
            <div className="stack sm">
              <div className="row wrap" style={{ gap: 8 }}><b className="small">{c.field}</b><Pill tone={tone}>{label}</Pill></div>
              <div className="diff-vals"><span className="before">{fmtVal(c.field, c.before)}</span>→<span>{fmtVal(c.field, c.after)}</span></div>
            </div>
          </div>
        )),
      )}
    </div>
  );
}

export function ProtocolPage() {
  const { workspace, mode } = useSession();
  const p = useAsync(() => getProtocol(workspace.protocolId!), [workspace.protocolId]);

  return (
    <>
      <PageHead eyebrow="Trust protocol" title="Authorization protocol"
        desc="What the agent may decide on its own. Signed by you and confirmed by Valio. Tightening applies at once; widening always needs a new signature." />
      {p.loading ? <Skeleton h={420} /> : p.error ? <ErrorState error={p.error} onRetry={p.reload} /> : (
        <div className="grid two" style={{ alignItems: 'start' }}>
          <ActiveProtocol p={p.data!} />
          <div className="stack lg">
            <AmendCard p={p.data!} />
            <DraftCard />
          </div>
        </div>
      )}
      {p.data && p.data.body.approval_mode !== mode && (
        <div style={{ marginTop: 16 }}>
          <Banner tone="info" title="Backend returned a different approval mode">
            The backend protocol for {workspace.name} says {p.data.body.approval_mode}; this workspace demos {mode}. The fixture server only seeds the hotel protocol.
          </Banner>
        </div>
      )}
    </>
  );
}

function ActiveProtocol({ p }: { p: Protocol }) {
  const st = PROTOCOL_STATUS[p.status];
  return (
    <section className="card stack" aria-labelledby="active-proto">
      <div className="row wrap">
        <div className="icon-tile ok"><BadgeCheck size={18} /></div>
        <div className="grow">
          <h2 id="active-proto">{p.id} <span className="muted" style={{ fontWeight: 500 }}>v{p.version}</span></h2>
          <div className="small muted">Agent {p.body.agent_id} · buyer {p.body.buyer_id}</div>
        </div>
        <Pill tone={st.tone}>{st.label}</Pill>
      </div>
      <div className="row wrap" style={{ gap: 6 }}>
        <Pill tone={p.buyer_signed_at ? 'ok' : 'neutral'}>{p.buyer_signed_at ? '✓' : '○'} Buyer signed</Pill>
        <Pill tone={p.valio_confirmed_at ? 'ok' : 'neutral'}>{p.valio_confirmed_at ? '✓' : '○'} Valio confirmed</Pill>
        {p.jws && <span className="mono xs muted" title={p.jws}>JWS {p.jws.slice(0, 22)}…</span>}
      </div>
      <ProtocolBodyView b={p.body} />
      <div className="xs muted">Statements in chat (“the manager already agreed”) never count as authorization. Only the signed version does.</div>
    </section>
  );
}

function AmendCard({ p }: { p: Protocol }) {
  const toast = useToast();
  const [limit, setLimit] = useState(String(Math.round(p.body.auto_approve_limit_cents / 100 * 0.8)));
  const [addCat, setAddCat] = useState(true);
  const [allPrices, setAllPrices] = useState(true);
  const [diff, setDiff] = useState<ProtocolDiff | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState<'signed' | 'rejected' | null>(null);

  const submit = async () => {
    const changes: Record<string, unknown> = { auto_approve_limit_cents: Math.round(Number(limit) * 100) };
    if (addCat) changes.allowed_categories = [...p.body.allowed_categories, '22540'];
    if (allPrices) changes['valio_scope.prices'] = 'ALL_CUSTOMERS';
    setBusy('amend');
    try {
      setDiff(await amendProtocol(p.id, changes));
      setDone(null);
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setBusy(null);
    }
  };

  const decide = async (decision: 'APPROVE' | 'REJECT') => {
    if (!diff?.approval_id) return;
    setBusy(decision);
    try {
      await decideApproval(diff.approval_id, { decision, changes: {}, note: null });
      setDone(decision === 'APPROVE' ? 'signed' : 'rejected');
      toast(decision === 'APPROVE' ? 'Signed. Waiting for Valio to confirm the new version.' : 'Expansion rejected; the tightening stays applied', decision === 'APPROVE' ? 'ok' : 'info');
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="card stack" aria-labelledby="amend-title">
      <div className="row">
        <div className="icon-tile warn"><FileSignature size={18} /></div>
        <div className="grow"><h2 id="amend-title">Change the protocol</h2><div className="small muted">Every change is sorted into one of three kinds</div></div>
      </div>
      <div className="row wrap" style={{ gap: 6 }}>
        <Pill tone="ok">Tighten → applies now</Pill><Pill tone="warn">Widen → needs signature</Pill><Pill tone="bad">Beyond buyer’s rights → rejected</Pill>
      </div>
      <div className="form-grid">
        <div className="field">
          <label htmlFor="limit">Auto-order up to (€)</label>
          <input id="limit" inputMode="numeric" value={limit} onChange={(e) => setLimit(e.target.value.replace(/\D/g, ''))} />
        </div>
      </div>
      <label className="row small" style={{ gap: 8 }}><input type="checkbox" checked={addCat} onChange={(e) => setAddCat(e.target.checked)} /> Also allow dry goods (22540)</label>
      <label className="row small" style={{ gap: 8 }}><input type="checkbox" checked={allPrices} onChange={(e) => setAllPrices(e.target.checked)} /> Let the agent see all customers’ prices</label>
      <Button onClick={submit} loading={busy === 'amend'} disabled={!limit}>Preview and submit change</Button>

      {diff && (
        <div className="stack fade-in">
          <DiffList d={diff} />
          {done === 'signed' && <Banner tone="ok" title="Expansion signed">A new version waits for Valio’s confirmation; the old one is superseded once confirmed.</Banner>}
          {done === 'rejected' && <Banner tone="info" title="Expansion rejected">The tightening stays applied.</Banner>}
          {!done && (diff.expanded?.length ?? 0) > 0 && (
            <div className="row wrap">
              <Button variant="primary" loading={busy === 'APPROVE'} onClick={() => decide('APPROVE')}>Sign the expansion</Button>
              <Button variant="ghost" loading={busy === 'REJECT'} onClick={() => decide('REJECT')}>Reject expansion</Button>
            </div>
          )}
          <div className="xs muted">Switching SEPARATE → SELF counts as a widening; SELF → SEPARATE tightens and applies at once. Only Valio can widen what an agent may see.</div>
        </div>
      )}
      <div className="xs muted mono">POST /v1/protocols/{p.id}/amend · POST /v1/approvals/{'{approval_id}'}/decision</div>
    </section>
  );
}

function DraftCard() {
  const { workspace } = useSession();
  const { flow, patch } = useFlow();
  const toast = useToast();
  const [text, setText] = useState('We order dairy every week; auto-order up to 1,000 euros, everything must be lactose-free.');
  const [draft, setDraft] = useState<Protocol | null>(flow.signedDraft);
  const [busy, setBusy] = useState<'draft' | 'sign' | null>(null);

  const make = async () => {
    setBusy('draft');
    try {
      setDraft(await draftProtocol(workspace.buyerId!, 'agt_hotel', text));
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setBusy(null);
    }
  };
  const sign = async () => {
    if (!draft) return;
    setBusy('sign');
    try {
      const signed = await signProtocol(draft.id);
      setDraft(signed);
      patch({ signedDraft: signed });
      toast('Signed. Valio confirms it from their review queue.', 'ok');
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="card stack" aria-labelledby="draft-title">
      <div className="row">
        <div className="icon-tile info"><FilePlus2 size={18} /></div>
        <div className="grow"><h2 id="draft-title">Draft a new version</h2><div className="small muted">The agent drafts; only a person can sign</div></div>
      </div>
      {!draft ? (
        <>
          <div className="field">
            <label htmlFor="draft-text">Describe the rules in one sentence</label>
            <textarea id="draft-text" rows={3} value={text} onChange={(e) => setText(e.target.value)} />
          </div>
          <Button onClick={make} loading={busy === 'draft'} disabled={!text.trim()}><Sparkles size={15} /> Draft it</Button>
        </>
      ) : (
        <div className="stack fade-in">
          <div className="row"><b className="grow">{draft.id} · v{draft.version}</b><Pill tone={PROTOCOL_STATUS[draft.status].tone}>{PROTOCOL_STATUS[draft.status].label}</Pill></div>
          {(draft.open_questions?.length ?? 0) > 0 && draft.status === 'DRAFT' && (
            <div className="stack sm">
              {draft.open_questions!.map((q) => (
                <Banner key={q.field} tone="warn" title={q.field}><span className="row top" style={{ gap: 6 }}><HelpCircle size={14} style={{ flex: 'none', marginTop: 2 }} />{q.question}</span></Banner>
              ))}
            </div>
          )}
          <div className="kv small">
            <div className="k">Auto-order up to</div><div>{eur(draft.body.auto_approve_limit_cents)}</div>
            <div className="k">Maximum</div><div>{eur(draft.body.max_order_cents)}</div>
            <div className="k">Must be</div><div>{(draft.body.hard_requirements ?? []).map((r) => r.label).join(', ')}</div>
          </div>
          {draft.status === 'DRAFT' ? (
            <div className="row"><Button variant="primary" loading={busy === 'sign'} onClick={sign}><FileSignature size={15} /> Sign draft</Button><Button variant="ghost" onClick={() => setDraft(null)}>Start over</Button></div>
          ) : (
            <Banner tone="info" title="Signed by the buyer">Waiting for Valio to confirm. The agent keeps using the active version until then.</Banner>
          )}
        </div>
      )}
      <div className="xs muted mono">POST /v1/protocols/draft · POST /v1/protocols/{'{id}'}/sign</div>
    </section>
  );
}
