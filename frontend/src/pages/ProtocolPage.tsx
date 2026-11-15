import { useState } from 'react';
import { ShieldAlert, ShieldCheck, ShieldPlus, Sparkles } from 'lucide-react';
import { amendProtocol, decideApproval, draftProtocol, getActiveProtocol, getFacets, signProtocol } from '../api/endpoints';
import { ProtocolEditor } from '../components/ProtocolEditor';
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
        <div className="k">Where</div><div>{b.approval_channel === 'EXTERNAL' ? 'Buyer’s own system (signed credential)' : 'PasStar approval page'}</div>
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
  const p = useAsync(() => getActiveProtocol(workspace.agentId!), [workspace.agentId]);

  return (
    <>
      <PageHead title="Authorization protocol"
        desc="What the agent may do on its own. Signed by you and Valio." />
      {p.loading ? <Skeleton h={420} /> : p.error ? <ErrorState error={p.error} onRetry={p.reload} /> : (
        <div className="grid two" style={{ alignItems: 'start' }}>
          <ActiveProtocol p={p.data!} />
          <div className="stack lg">
            <AmendCard p={p.data!} onChanged={p.reload} />
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

function AmendCard({ p, onChanged }: { p: Protocol; onChanged: () => void }) {
  const toast = useToast();
  const facets = useAsync(getFacets, []);
  const [diff, setDiff] = useState<ProtocolDiff | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState<'signed' | 'rejected' | null>(null);
  const [round, setRound] = useState(0);        // a new form after each submitted change

  /** Send only what changed; PasStar applies stricter terms at once and puts looser ones up for signature. */
  const submit = async (edited: ProtocolBody) => {
    const fields = ['allowed_categories', 'hard_requirements', 'auto_approve_limit_cents', 'max_order_cents', 'valid_until'] as const;
    const changes: Record<string, unknown> = {};
    for (const f of fields) {
      if (JSON.stringify(edited[f]) !== JSON.stringify(p.body[f])) changes[f] = edited[f];
    }
    if (!Object.keys(changes).length) {
      toast('Nothing changed', 'info');
      return;
    }
    try {
      setDiff(await amendProtocol(p.id, changes));
      setDone(null);
      setRound((r) => r + 1);
      onChanged();
    } catch (e) {
      toast(describeError(e), 'bad');
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
        <div className="small muted">Change any term below. Stricter terms apply at once; looser ones need your signature and Valio’s confirmation.</div>
      </div>
      {facets.loading ? <Skeleton h={220} /> : facets.error ? <ErrorState error={facets.error} onRetry={facets.reload} /> : (
        <ProtocolEditor key={`${p.id}-${round}`} body={p.body} facets={facets.data!} onSave={submit} saveLabel="Submit change" />
      )}

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
    if (!text.trim() || busy) return;
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
          <Button onClick={make} loading={busy === 'draft'}><Sparkles size={15} /> Draft it</Button>
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
