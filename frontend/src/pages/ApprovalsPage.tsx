import { useEffect, useState } from 'react';
import { ArrowRight, Check, ClipboardCheck, FlaskConical, Lightbulb, X } from 'lucide-react';
import { decideApproval, listApprovals, listFaults, setFault } from '../api/endpoints';
import { ApiError, describeError } from '../api/client';
import type { ApprovalCard } from '../api/types';
import { accountByUserId } from '../config/accounts';
import { APPROVAL_STATUS, eur, levelOf, outcomeOf } from '../lib/format';
import { useFlow } from '../state/flow';
import { useSession } from '../state/session';
import { TrustMeter } from '../components/CandidateCard';
import { DiffList } from './ProtocolPage';
import { Avatar, Banner, Button, Empty, ErrorState, PageHead, Pill, Skeleton, Switch, useAsync, useToast } from '../components/ui';

export function ApprovalsPage() {
  const { account, mode } = useSession();
  const { flow } = useFlow();
  const list = useAsync(() => listApprovals('BUYER'), [account.key, flow.order?.id]);
  const [activeId, setActiveId] = useState<string | null>(null);

  const items = (list.data ?? []).map((a) => (flow.decisions[a.id] ? flow.decisions[a.id].approval : a));
  const sorted = [...items].sort((a, b) => Number(b.status === 'PENDING') - Number(a.status === 'PENDING'));
  const active = sorted.find((a) => a.id === activeId) ?? sorted[0];

  return (
    <>
      <PageHead
        eyebrow={mode === 'SELF' ? 'Approvals · single owner' : 'Approvals · organisation'}
        title={mode === 'SELF' ? 'Needs your approval' : 'Approvals'}
        desc={mode === 'SELF'
          ? 'The agent stops whenever an order goes outside your protocol. Approving is a separate step and is logged as self-approved.'
          : 'Orders the agent could not place on its own. You are the approver named in the protocol.'}
      />
      {list.loading ? <Skeleton h={360} /> : list.error ? <ErrorState error={list.error} onRetry={list.reload} /> : !active ? (
        <Empty icon={<ClipboardCheck size={22} />} title="Nothing waiting">Orders within the protocol are placed automatically. Exceptions show up here.</Empty>
      ) : (
        <div className="stack lg">
          {sorted.length > 1 && (
            <div className="grid three">
              {sorted.map((a) => (
                <button key={a.id} className="list-item" aria-current={a.id === active.id} onClick={() => setActiveId(a.id)}>
                  <span className="grow strong small">{a.title}</span>
                  <Pill tone={APPROVAL_STATUS[a.status].tone}>{APPROVAL_STATUS[a.status].label}</Pill>
                </button>
              ))}
            </div>
          )}
          <ApprovalDetail key={active.id} a={active} />
        </div>
      )}
    </>
  );
}

function ApprovalDetail({ a }: { a: ApprovalCard }) {
  const { account, mode } = useSession();
  const { flow, patch } = useFlow();
  const toast = useToast();
  const [busy, setBusy] = useState<'APPROVE' | 'REJECT' | null>(null);
  const [note, setNote] = useState('');
  const [error, setError] = useState<ApiError | null>(null);
  const [priceChange, setPriceChange] = useState(false);
  const decided = flow.decisions[a.id];
  const status = APPROVAL_STATUS[a.status];
  const c = a.candidate;
  const requester = accountByUserId(a.requested_by);
  const approver = accountByUserId(a.approver);
  const selfMode = (a.approval_mode ?? mode) === 'SELF';

  useEffect(() => {
    listFaults().then((fs) => setPriceChange(!!fs.find((f) => f.name === 'PRICE_CHANGE')?.enabled)).catch(() => undefined);
  }, []);

  const toggleFault = async (v: boolean) => {
    setPriceChange(v);
    try {
      await setFault('PRICE_CHANGE', v);
      toast(v ? 'Fault on: Valio’s price will change before the re-check' : 'Fault off', 'info');
    } catch (e) {
      setPriceChange(!v);
      toast(describeError(e), 'bad');
    }
  };

  const decide = async (decision: 'APPROVE' | 'REJECT') => {
    setBusy(decision);
    setError(null);
    try {
      const res = await decideApproval(a.id, { decision, note: note || null, changes: {} });
      const o = res.order;
      // The M1 fixture server always re-checks fine; only the mock (or a real backend) reports a price change.
      const requote = o?.status === 'REQUOTE_REQUIRED';
      patch({
        decisions: { ...flow.decisions, [a.id]: res },
        order: o ?? flow.order,
        approvalResult: decision === 'REJECT' ? 'REJECTED' : requote ? 'REQUOTE' : 'APPROVED',
      });
      toast(decision === 'REJECT' ? 'Rejected. The requester is told why.' : requote ? 'Approved, but the re-check found a price change' : `Re-check passed; order ${o?.id ?? ''} placed${selfMode ? ' (logged as self-approved)' : ''}`, decision === 'REJECT' || requote ? 'warn' : 'ok');
    } catch (e) {
      if (e instanceof ApiError) setError(e);
      toast(describeError(e), 'bad');
    } finally {
      setBusy(null);
    }
  };

  return (
    <article className="card stack lg" aria-labelledby={`apr-${a.id}`}>
      <div className="row wrap">
        <h2 id={`apr-${a.id}`} className="grow" style={{ fontSize: 20 }}>{a.title}</h2>
        <Pill tone={status.tone}>{status.label}</Pill>
      </div>

      <div className="flow-people">
        {requester && <Avatar role={requester.key} name={requester.name} size="sm" />}
        <span className="small"><span className="muted">Requested by</span> <b>{requester ? `${requester.name} · ${requester.title}` : a.requested_by ?? '—'}</b></span>
        <ArrowRight size={16} className="arrow" />
        {approver && <Avatar role={approver.key} name={approver.name} size="sm" />}
        <span className="small"><span className="muted">Approver in protocol</span> <b>{a.approver === account.userId ? 'you' : approver ? approver.name : a.approver}</b></span>
        <Pill tone={selfMode ? 'info' : 'neutral'}>{selfMode ? 'SELF · logged as self-approved' : 'SEPARATE · requester cannot approve'}</Pill>
      </div>

      <div className="grid two" style={{ alignItems: 'start' }}>
        <div className="stack">
          <section className="stack sm">
            <h3>{selfMode ? 'Why the agent stopped here' : 'Why this needs your approval'}</h3>
            {a.reasons.map((r) => <div key={r} className="small text-2">• {r}</div>)}
            <div className="row wrap" style={{ gap: 6 }}>{a.reason_codes.map((r) => <span key={r} className="pill tone-neutral mono">{r}</span>)}</div>
          </section>
          {(a.risk_tags?.length ?? 0) > 0 && (
            <section className="stack sm">
              <h3>Risks</h3>
              {a.risk_tags!.map((t, i) => <div key={i} className="row" style={{ gap: 8 }}><Pill tone={levelOf(t.level).tone}>{t.kind.toLowerCase()} · {levelOf(t.level).label}</Pill><span className="small">{t.text}</span></div>)}
            </section>
          )}
          {a.raw_text && (
            <section className="stack sm">
              <h3>Original request</h3>
              <blockquote className="small text-2" style={{ margin: 0, paddingLeft: 12, borderLeft: '3px solid var(--border-strong)' }}>“{a.raw_text}”</blockquote>
            </section>
          )}
          <div className="card inset tight row top" style={{ gap: 10 }}>
            <Lightbulb size={18} color="var(--accent)" style={{ flex: 'none', marginTop: 2 }} />
            <div className="small"><b>Suggestion: {a.suggested_action === 'APPROVE' ? 'approve' : a.suggested_action === 'REJECT' ? 'reject' : 'route'}</b><div className="text-2">{a.suggested_reason}</div></div>
          </div>
        </div>

        <div className="stack">
          {c && (
            <section className="card inset stack sm">
              <div className="xs muted">Chosen option</div>
              <div className="strong">{c.lines[0].name}</div>
              <div className="row between">
                <span style={{ fontSize: 24, fontWeight: 780, letterSpacing: '-0.02em' }}>{eur(c.totals?.gross_cents)}</span>
                <Pill tone={levelOf(c.cards.trust.overall).tone}>Trust {levelOf(c.cards.trust.overall).label}</Pill>
              </div>
              <div className="small text-2">{c.explanation}</div>
              <TrustMeter t={c.cards.trust} />
            </section>
          )}
          {(a.alternatives?.length ?? 0) > 0 && (
            <section className="stack sm">
              <h3>Alternatives</h3>
              {a.alternatives!.map((x) => {
                const o = outcomeOf(x.outcome_preview, selfMode ? 'SELF' : 'SEPARATE');
                return (
                  <div key={x.id} className="row wrap small" style={{ gap: 8 }}>
                    <span className="grow">{x.lines[0].name}</span>
                    <b className="nowrap">{eur(x.totals?.gross_cents)}</b>
                    <Pill tone={o.tone}>{o.label}</Pill>
                  </div>
                );
              })}
            </section>
          )}
          {a.protocol_diff && <DiffList d={a.protocol_diff} />}
        </div>
      </div>

      {error && (
        <Banner tone="bad" title={`${error.status} ${error.code}`}>{describeError(error)}</Banner>
      )}
      {decided?.order?.status === 'REQUOTE_REQUIRED' && (
        <Banner tone="warn" title="Price changed: the old option is blocked">
          {decided.order.requote_reason ?? 'The re-check after approval found a different price.'} A new quote {decided.order.new_quote_id ? `(${decided.order.new_quote_id}) ` : ''}was issued and needs confirmation. What you approved and what gets ordered must match.
        </Banner>
      )}
      {decided?.order?.status === 'PLACED' && <Banner tone="ok" title={`Approved · order ${decided.order.id} placed`}>Price and stock were re-checked after your approval and had not changed.</Banner>}
      {a.status === 'REJECTED' && <Banner tone="bad" title="Rejected">The requester sees this on their orders page.</Banner>}

      {a.status === 'PENDING' && (
        <div className="stack">
          <div className="field">
            <label htmlFor="note">Note to the requester <span className="muted">(optional)</span></label>
            <input id="note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. OK this time, prefer the 10 L packs next week" />
          </div>
          <div className="row wrap">
            <Button variant="primary" loading={busy === 'APPROVE'} disabled={!!busy} onClick={() => decide('APPROVE')}><Check size={16} /> Approve</Button>
            <Button variant="outline-danger" loading={busy === 'REJECT'} disabled={!!busy} onClick={() => decide('REJECT')}><X size={16} /> Reject</Button>
            <label className="row ml-auto small muted" style={{ gap: 8 }}>
              <FlaskConical size={15} /> Demo: price changes before the re-check
              <Switch checked={priceChange} onChange={toggleFault} label="Price changes before re-check" />
            </label>
          </div>
        </div>
      )}
      <div className="xs muted mono">GET /v1/approvals?kind=BUYER · POST /v1/approvals/{a.id}/decision · PUT /v1/admin/faults/PRICE_CHANGE</div>
    </article>
  );
}
