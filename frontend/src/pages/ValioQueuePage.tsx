import { useState } from 'react';
import { ChevronDown, Eye, FileSearch, Inbox, Route, ShieldCheck, ShieldX, ThumbsUp } from 'lucide-react';
import { confirmProtocol, decideApproval, getFacets, listApprovals, listProtocols } from '../api/endpoints';
import { ProtocolTerms, buyerName } from '../components/ProtocolTerms';
import { describeError } from '../api/client';
import type { ApprovalCard, Facets, Protocol } from '../api/types';
import { APPROVAL_STATUS, eur, levelOf } from '../lib/format';
import { useFlow } from '../state/flow';
import { Banner, Button, Empty, ErrorState, PageHead, Pill, Skeleton, useAsync, useToast } from '../components/ui';

export function ValioQueuePage() {
  const { flow } = useFlow();
  const list = useAsync(() => listApprovals('VALIO'), []);
  const items = (list.data ?? []).map((a) => flow.decisions[a.id]?.approval ?? a);
  // Protocols the buyer signed, from the backend: a first protocol (step 0) or a raised limit (a new version).
  const toConfirm = useAsync(() => listProtocols('PENDING_VALIO_CONFIRMATION'), []);
  const active = useAsync(() => listProtocols('ACTIVE'), []);
  const facets = useAsync(getFacets, []);
  const recent = [...(active.data ?? [])].filter((p) => p.valio_confirmed_at)
    .sort((a, b) => (b.valio_confirmed_at ?? '').localeCompare(a.valio_confirmed_at ?? '')).slice(0, 5);
  const confirmed = () => { toConfirm.reload(); active.reload(); };

  return (
    <>
      <PageHead title="Review queue"
        desc="Requests the channel won’t answer on its own." />
      <div className="stack lg">
        {(toConfirm.data ?? []).map((p) => <ConfirmProtocolCard key={p.id} d={p} facets={facets.data} onDone={confirmed} />)}
        {list.loading ? <Skeleton h={380} /> : list.error ? <ErrorState error={list.error} onRetry={list.reload} /> : !items.length ? (
          <Empty icon={<Inbox size={22} />} title="Queue is empty">Nothing needs a Valio decision right now.</Empty>
        ) : items.map((a) => <ValioCard key={a.id} a={a} />)}
        <RecentlyConfirmed items={recent} facets={facets.data} />
      </div>
    </>
  );
}

/** A protocol a buyer signed. Valio reads every term first; only then can it confirm (and sign) it. */
function ConfirmProtocolCard({ d, facets, onDone }: { d: Protocol; facets?: Facets | null; onDone: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const confirm = async () => {
    setBusy(true);
    try {
      await confirmProtocol(d.id);
      toast(`${buyerName(d.body.buyer_id)}: protocol confirmed and in force`, 'ok');
      onDone();
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card stack confirm-card">
      <div className="row wrap" style={{ gap: 8 }}>
        <div className="grow">
          <div className="eyebrow">{d.supersedes ? 'Change to a protocol' : 'New protocol'} · waiting for Valio</div>
          <h3>{buyerName(d.body.buyer_id)} signed rules for its AI agent</h3>
          <div className="small muted">Orders alone up to {eur(d.body.auto_approve_limit_cents)} · never above {eur(d.body.max_order_cents)} · {d.id} v{d.version}</div>
        </div>
        {!open && <Button onClick={() => setOpen(true)}><FileSearch size={15} /> Review the protocol</Button>}
      </div>
      {open && (
        <>
          <ProtocolTerms p={d} facets={facets} />
          <Banner tone="info" title="By confirming, Valio signs these rules">
            From then on Valio accepts this agent’s orders within them, and refuses anything outside.
          </Banner>
          <div className="row wrap" style={{ gap: 8 }}>
            <Button variant="primary" loading={busy} onClick={confirm}><ShieldCheck size={15} /> Confirm and sign</Button>
            <Button variant="ghost" onClick={() => setOpen(false)}>Not now</Button>
          </div>
        </>
      )}
    </section>
  );
}

/** Protocols Valio confirmed most recently, newest first, with their terms one click away. */
function RecentlyConfirmed({ items, facets }: { items: Protocol[]; facets?: Facets | null }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!items.length) return null;
  return (
    <section className="card stack">
      <h2>Recently confirmed</h2>
      <ul className="confirmed-list">
        {items.map((p) => (
          <li key={p.id}>
            <button type="button" className="confirmed-row" aria-expanded={open === p.id} onClick={() => setOpen(open === p.id ? null : p.id)}>
              <ShieldCheck size={16} className="ok-ink" aria-hidden />
              <span className="grow"><b>{buyerName(p.body.buyer_id)}</b> · {p.id} v{p.version}
                <span className="small muted"> · alone up to {eur(p.body.auto_approve_limit_cents)}, never above {eur(p.body.max_order_cents)}</span></span>
              <span className="small muted">{p.valio_confirmed_at ? new Date(p.valio_confirmed_at).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : ''}</span>
              <ChevronDown size={15} className="chev" aria-hidden />
            </button>
            {open === p.id && <ProtocolTerms p={p} facets={facets} />}
          </li>
        ))}
      </ul>
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
      // The backend answers with this card as decided. The in-browser mock answers with a buyer approval
      // instead, so there keep this card and take only the new status.
      const status: ApprovalCard['status'] = decision === 'APPROVE' ? 'APPROVED' : decision === 'REJECT' ? 'REJECTED' : 'ROUTED';
      const approval = res.approval.id === a.id ? res.approval : { ...a, status };
      patch({ decisions: { ...flow.decisions, [a.id]: { ...res, approval } } });
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
