import { useState } from 'react';
import { ShieldAlert, ShieldCheck, ShieldPlus, Sparkles } from 'lucide-react';
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
    ['tightened', 'ok', 'Applied', <ShieldCheck key="i" size={16} />],
    ['expanded', 'warn', 'Needs your signature', <ShieldPlus key="i" size={16} />],
    ['forbidden', 'bad', 'Not allowed', <ShieldAlert key="i" size={16} />],
  ];
  return (
    <div className="stack sm">
      {groups.map(([k, tone, label, icon]) =>
        (d[k] ?? []).map((c) => (
          <div key={k + c.field} className="diff-row">
            <div className={`diff-icon ${tone}`}>{icon}</div>
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
  const { workspace } = useSession();
  const p = useAsync(() => getProtocol(workspace.protocolId!), [workspace.protocolId]);

  return (
    <>
      <PageHead title="Authorization protocol"
        desc="What the agent may do on its own. Signed by you and Valio." />
      {p.loading ? <Skeleton h={420} /> : p.error ? <ErrorState error={p.error} onRetry={p.reload} /> : (
        <div className="grid two" style={{ alignItems: 'start' }}>
          <ActiveProtocol p={p.data!} />
          <div className="stack lg">
            <AmendCard p={p.data!} />
            <DraftCard />
          </div>
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
        <div className="grow">
          <h2 id="active-proto">Current rules</h2>
          <div className="small muted">Version {p.version}{p.buyer_signed_at && p.valio_confirmed_at ? ' · signed by you and Valio' : ''}</div>
        </div>
        <Pill tone={st.tone}>{st.label}</Pill>
      </div>
      <ProtocolBodyView b={p.body} />
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
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="card stack" aria-labelledby="amend-title">
      <div>
        <h2 id="amend-title">Change the rules</h2>
        <div className="small muted">Stricter rules apply at once. Looser rules need your signature.</div>
      </div>
      <div className="form-grid">
        <div className="field">
          <label htmlFor="limit">Auto-order up to (€)</label>
          <input id="limit" inputMode="numeric" value={limit} onChange={(e) => setLimit(e.target.value.replace(/\D/g, ''))} />
        </div>
      </div>
      <label className="row small" style={{ gap: 8 }}><input type="checkbox" checked={addCat} onChange={(e) => setAddCat(e.target.checked)} /> Also allow dry goods (22540)</label>
      <label className="row small" style={{ gap: 8 }}><input type="checkbox" checked={allPrices} onChange={(e) => setAllPrices(e.target.checked)} /> Let the agent see all customers’ prices</label>
      <Button onClick={submit} loading={busy === 'amend'} disabled={!limit}>Submit change</Button>

      {diff && (
        <div className="stack">
          <DiffList d={diff} />
          {done === 'signed' && <Banner tone="ok" title="Signed">Valio confirms it next.</Banner>}
          {done === 'rejected' && <Banner tone="info" title="Not signed">Only the stricter changes apply.</Banner>}
          {!done && (diff.expanded?.length ?? 0) > 0 && (
            <div className="row wrap">
              <Button variant="primary" loading={busy === 'APPROVE'} onClick={() => decide('APPROVE')}>Sign</Button>
              <Button variant="ghost" loading={busy === 'REJECT'} onClick={() => decide('REJECT')}>Don’t sign</Button>
            </div>
          )}
        </div>
      )}
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
      setDraft(await draftProtocol(workspace.buyerId!, workspace.agentId!, text));
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
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="card stack" aria-labelledby="draft-title">
      <div>
        <h2 id="draft-title">Write new rules</h2>
        <div className="small muted">Describe them; the agent drafts, you sign.</div>
      </div>
      {!draft ? (
        <>
          <div className="field">
            <textarea aria-label="Describe the rules" id="draft-text" rows={3} value={text} onChange={(e) => setText(e.target.value)} />
          </div>
          <Button onClick={make} loading={busy === 'draft'} disabled={!text.trim()}><Sparkles size={15} /> Draft it</Button>
        </>
      ) : (
        <div className="stack">
          <div className="row"><b className="grow">Draft, version {draft.version}</b><Pill tone={PROTOCOL_STATUS[draft.status].tone}>{PROTOCOL_STATUS[draft.status].label}</Pill></div>
          {(draft.open_questions?.length ?? 0) > 0 && draft.status === 'DRAFT' && (
            <div className="stack sm">
              {draft.open_questions!.map((q) => (
                <Banner key={q.field} tone="warn" title={q.question} />
              ))}
            </div>
          )}
          <div className="kv small">
            <div className="k">Auto-order up to</div><div>{eur(draft.body.auto_approve_limit_cents)}</div>
            <div className="k">Maximum</div><div>{eur(draft.body.max_order_cents)}</div>
            <div className="k">Must be</div><div>{(draft.body.hard_requirements ?? []).map((r) => r.label).join(', ')}</div>
          </div>
          {draft.status === 'DRAFT' ? (
            <div className="row"><Button variant="primary" loading={busy === 'sign'} onClick={sign}>Sign</Button><Button variant="ghost" onClick={() => setDraft(null)}>Start over</Button></div>
          ) : (
            <Banner tone="info" title="Signed. Waiting for Valio to confirm.">Until then the current rules apply.</Banner>
          )}
        </div>
      )}
    </section>
  );
}
