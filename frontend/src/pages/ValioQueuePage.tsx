import { useState } from 'react';
import { Eye, Inbox, Route, ShieldX, ThumbsUp } from 'lucide-react';
import { confirmProtocol, decideApproval, listApprovals } from '../api/endpoints';
import { describeError } from '../api/client';
import type { ApprovalCard } from '../api/types';
import { APPROVAL_STATUS, eur, levelOf } from '../lib/format';
import { useFlow } from '../state/flow';
import { Banner, Button, Empty, ErrorState, PageHead, Pill, Skeleton, useAsync, useToast } from '../components/ui';

export function ValioQueuePage() {
  const { flow } = useFlow();
  const list = useAsync(() => listApprovals('VALIO'), []);
  const items = (list.data ?? []).map((a) => flow.decisions[a.id]?.approval ?? a);

  return (
    <>
      <PageHead title="Review queue"
        desc="Requests the channel won’t answer on its own." />
      <div className="stack lg">
        {flow.signedDraft && <ConfirmProtocolCard />}
        {list.loading ? <Skeleton h={380} /> : list.error ? <ErrorState error={list.error} onRetry={list.reload} /> : !items.length ? (
          <Empty icon={<Inbox size={22} />} title="Queue is empty">Nothing needs a Valio decision right now.</Empty>
        ) : items.map((a) => <ValioCard key={a.id} a={a} />)}
      </div>
    </>
  );
}

function ConfirmProtocolCard() {
  const { flow, patch } = useFlow();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const d = flow.signedDraft!;
  if (d.status === 'ACTIVE') return <Banner tone="ok" title={`${d.id} confirmed and active`}>The previous version is superseded.</Banner>;
  const confirm = async () => {
    setBusy(true);
    try {
      patch({ signedDraft: await confirmProtocol(d.id) });
      toast('Protocol confirmed and active', 'ok');
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card row wrap">
      <div className="grow">
        <h3>New protocol {d.id} signed by the buyer</h3>
        <div className="small muted">Auto-order up to {eur(d.body.auto_approve_limit_cents)} · max {eur(d.body.max_order_cents)} · confirm the customer and set Valio’s scope</div>
      </div>
      <Button variant="primary" loading={busy} onClick={confirm}>Confirm protocol</Button>
    </section>
  );
}

function ValioCard({ a }: { a: ApprovalCard }) {
  const { flow, patch } = useFlow();
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const ag = a.agent_activity;
  const st = APPROVAL_STATUS[a.status];

  const decide = async (decision: 'APPROVE' | 'REJECT' | 'ROUTE') => {
    setBusy(decision);
    try {
      const res = await decideApproval(a.id, { decision, route_to: decision === 'ROUTE' ? 'data_governance' : null, changes: {}, note: null });
      // The fixture server answers with the buyer approval; keep this card and take only the new status.
      const status: ApprovalCard['status'] = decision === 'APPROVE' ? 'APPROVED' : decision === 'REJECT' ? 'REJECTED' : 'ROUTED';
      patch({ decisions: { ...flow.decisions, [a.id]: { ...res, approval: { ...a, status } } } });
      toast(decision === 'REJECT' ? 'Rejected. Nothing was disclosed.' : decision === 'ROUTE' ? 'Routed to the data governance team' : 'One-off disclosure approved and logged', decision === 'APPROVE' ? 'warn' : 'ok');
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setBusy(null);
    }
  };

  return (
    <article className="card stack lg">
      <div className="row wrap">
        <h2 className="grow">{a.title}</h2>
        <Pill tone={st.tone}>{st.label}</Pill>
      </div>

      <div className="grid two" style={{ alignItems: 'start' }}>
        <div className="stack">
          <section className="stack sm">
            <h3>Why this came to Valio</h3>
            {a.reasons.map((r) => <div key={r} className="small text-2">• {r}</div>)}
          </section>
          <section className="stack sm">
            <h3>Risks</h3>
            {(a.risk_tags ?? []).map((t, i) => <div key={i} className="row" style={{ gap: 8 }}><Pill tone={levelOf(t.level).tone}>{t.kind.toLowerCase()}</Pill><span className="small">{t.text}</span></div>)}
          </section>
          <section className="stack sm">
            <h3 className="row" style={{ gap: 6 }}><Eye size={15} /> If approved, this would be disclosed</h3>
            <div className="evidence">{(a.disclosure_preview ?? []).map((f) => <Pill key={f.field} tone="bad">{f.field} · {f.sensitivity}</Pill>)}</div>
          </section>
        </div>
        <div className="stack">
          {ag && (
            <section className="stack sm">
              <h3>Agent behaviour · 24 h</h3>
              <div className="small muted">{ag.name} · <span className="mono">{ag.agent_id}</span></div>
              <div className="stats">
                <div className="stat"><div className="v">{ag.requests_24h}</div><div className="l">Requests</div></div>
                <div className="stat"><div className="v" style={{ color: 'var(--warn)' }}>{ag.blocked_24h}</div><div className="l">Blocked</div></div>
                <div className="stat"><div className="v" style={{ color: 'var(--bad)' }}>{ag.probe_alerts_24h}</div><div className="l">Probe alerts</div></div>
              </div>
            </section>
          )}
          <p className="small"><span className="muted">Suggested: </span><b>{a.suggested_action.toLowerCase()}</b>. {a.suggested_reason}.</p>
        </div>
      </div>

      {a.status === 'PENDING' ? (
        <div className="row wrap">
          <Button variant="primary" loading={busy === 'REJECT'} disabled={!!busy} onClick={() => decide('REJECT')}><ShieldX size={16} /> Reject</Button>
          <Button loading={busy === 'APPROVE'} disabled={!!busy} onClick={() => decide('APPROVE')}><ThumbsUp size={15} /> Approve one-off disclosure</Button>
          <Button variant="ghost" loading={busy === 'ROUTE'} disabled={!!busy} onClick={() => decide('ROUTE')}><Route size={15} /> Route to data team</Button>
        </div>
      ) : (
        <Banner tone={a.status === 'REJECTED' ? 'ok' : 'info'} title={st.label}>
          {a.status === 'REJECTED' ? 'Nothing was disclosed. The decision is in the audit log.' : 'Logged in the audit trail.'}
        </Banner>
      )}
    </article>
  );
}
