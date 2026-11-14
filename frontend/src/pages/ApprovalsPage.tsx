import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, ClipboardCheck, X } from 'lucide-react';
import { decideApproval, listApprovals } from '../api/endpoints';
import { ApiError, describeError } from '../api/client';
import type { ApprovalCard } from '../api/types';
import { accountByUserId } from '../config/accounts';
import { APPROVAL_STATUS, eur } from '../lib/format';
import { useFlow } from '../state/flow';
import { useSession } from '../state/session';
import { DiffList } from './ProtocolPage';
import { Banner, Button, Empty, ErrorState, PageHead, Pill, Skeleton, useAsync, useToast } from '../components/ui';

export function ApprovalsPage() {
  const { account, mode } = useSession();
  const { flow } = useFlow();
  const list = useAsync(() => listApprovals('BUYER'), [account.key, flow.order?.id]);
  const [activeId, setActiveId] = useState<string | null>(null);

  const items = (list.data ?? []).map((a) => flow.decisions[a.id]?.approval ?? a);
  const sorted = [...items].sort((a, b) => Number(b.status === 'PENDING') - Number(a.status === 'PENDING'));
  const active = sorted.find((a) => a.id === activeId) ?? sorted[0];

  return (
    <div className="narrow">
      <PageHead title={mode === 'SELF' ? 'Needs your approval' : 'Approvals'} />
      {list.loading ? <Skeleton h={360} /> : list.error ? <ErrorState error={list.error} onRetry={list.reload} /> : !active ? (
        <Empty icon={<ClipboardCheck size={22} />} title="Nothing to approve">Orders within your protocol are placed automatically.</Empty>
      ) : (
        <div className="stack lg">
          {sorted.length > 1 && (
            <div className="stack sm">
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
    </div>
  );
}

function ApprovalDetail({ a }: { a: ApprovalCard }) {
  const { account } = useSession();
  const { flow, patch } = useFlow();
  const toast = useToast();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const decided = flow.decisions[a.id];
  const c = a.candidate;
  const requester = accountByUserId(a.requested_by);
  const byMe = a.requested_by === account.userId;

  // Approving always goes through the review page; rejecting orders nothing, so it can happen here.
  const reject = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await decideApproval(a.id, { decision: 'REJECT', note: null, changes: {} });
      patch({ decisions: { ...flow.decisions, [a.id]: res }, approvalResult: 'REJECTED' });
    } catch (e) {
      if (e instanceof ApiError) setError(e);
      else toast(describeError(e), 'bad');
    } finally {
      setBusy(false);
    }
  };

  return (
    <article className="card stack lg">
      <div className="stack sm">
        <div className="row wrap">
          <h2 className="grow">{a.title}</h2>
          {a.status !== 'PENDING' && <Pill tone={APPROVAL_STATUS[a.status].tone}>{APPROVAL_STATUS[a.status].label}</Pill>}
        </div>
        <p className="small muted">
          {byMe ? 'You asked' : `${requester ? `${requester.name} (${requester.title.toLowerCase()})` : a.requested_by} asked`}
          {a.raw_text ? <>: “{a.raw_text}”</> : null}
        </p>
      </div>

      <div className="grid two" style={{ alignItems: 'start' }}>
        <section className="stack sm">
          <h3>Why it needs approval</h3>
          {a.reasons.map((r) => <p key={r} className="small text-2">{r}</p>)}
          {(a.risk_tags ?? []).map((t, i) => <p key={i} className="small text-2">{t.text}</p>)}
          <p className="small" style={{ marginTop: 6 }}>
            <span className="muted">Suggested: </span><b>{a.suggested_action === 'APPROVE' ? 'approve' : a.suggested_action === 'REJECT' ? 'reject' : 'route'}</b>. {a.suggested_reason}.
          </p>
        </section>

        {c && (
          <section className="stack sm">
            <h3>Option</h3>
            <div className="row top between">
              <span className="small">{c.lines[0].name}</span>
              <b className="nowrap">{eur(c.totals?.gross_cents)}</b>
            </div>
            <p className="xs muted">{c.explanation}</p>
            {(a.alternatives?.length ?? 0) > 0 && <h3 style={{ marginTop: 10 }}>Alternatives within the limit</h3>}
            {a.alternatives?.map((x) => (
              <div key={x.id} className="row top between">
                <span className="small text-2">{x.lines[0].name}</span>
                <span className="small nowrap">{eur(x.totals?.gross_cents)}</span>
              </div>
            ))}
          </section>
        )}
      </div>
      {a.protocol_diff && <DiffList d={a.protocol_diff} />}

      {error && <Banner tone="bad" title="Can’t approve this">{describeError(error)}</Banner>}
      {decided?.order?.status === 'REQUOTE_REQUIRED' && (
        <Banner tone="warn" title="The price changed, so nothing was ordered">
          {decided.order.requote_reason ?? 'The re-check after approval found a different price.'} The requester has to confirm a new price.
        </Banner>
      )}
      {decided?.order?.status === 'PLACED' && <Banner tone="ok" title="Approved and ordered">Price and stock were checked again and hadn’t changed.</Banner>}
      {a.status === 'REJECTED' && <Banner tone="bad" title="Rejected" />}

      {a.status === 'PENDING' && (
        <div className="stack">
          <div className="row wrap">
            <Button variant="primary" disabled={busy} onClick={() => navigate(`/approve?id=${encodeURIComponent(a.id)}`)}>Review and approve <ArrowRight size={16} /></Button>
            <Button variant="outline-danger" loading={busy} onClick={reject}><X size={16} /> Reject</Button>
          </div>
        </div>
      )}
    </article>
  );
}
