import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Lock, Package, RefreshCw } from 'lucide-react';
import { getOrder } from '../api/endpoints';
import { describeError } from '../api/client';
import { dayLabel, eur, num, ORDER_STATUS, time } from '../lib/format';
import { useFlow } from '../state/flow';
import { useSession } from '../state/session';
import { Banner, Button, Empty, PageHead, Pill, useToast } from '../components/ui';

export function OrdersPage() {
  const { flow, patch } = useFlow();
  const { mode } = useSession();
  const navigate = useNavigate();
  const toast = useToast();
  const [refreshing, setRefreshing] = useState(false);
  const o = flow.order;
  const s = flow.request?.structured;
  const sel = flow.selection;

  const refresh = async () => {
    if (!o) return;
    setRefreshing(true);
    try {
      const fresh = await getOrder(o.id);
      // The M1 fixture server returns ord_1 for any id; only accept a matching order.
      if (fresh.id === o.id) patch({ order: fresh });
      toast('Status refreshed', 'info');
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setRefreshing(false);
    }
  };

  if (!o && !flow.sentToApproval) {
    return (
      <>
        <PageHead eyebrow="Orders" title="My orders" />
        <Empty icon={<Package size={22} />} title="No orders yet"
          action={<Button variant="primary" onClick={() => navigate(flow.request ? '/options' : '/request')}>{flow.request ? 'Back to options' : 'Start a request'}</Button>}>
          Choose an option and place the order. Orders above the auto-approve limit wait here for the procurement manager.
        </Empty>
      </>
    );
  }

  const status = o ? ORDER_STATUS[o.status] : { label: 'Waiting for approval', tone: 'warn' as const };
  const pending = !o || o.status === 'PENDING_BUYER_APPROVAL';

  return (
    <>
      <PageHead eyebrow="Orders" title="My orders" desc="Everything you submitted from this workspace, with its live status."
        actions={o && <Button variant="ghost" onClick={refresh} loading={refreshing}><RefreshCw size={15} /> Refresh</Button>} />

      <article className="card stack">
        <div className="row wrap">
          <div className="icon-tile"><Package size={18} /></div>
          <div className="grow">
            <div className="strong">{s ? `${num(s.quantity.value)} ${s.quantity.unit} ${s.category_label?.toLowerCase() ?? ''}, ${(s.requirements ?? []).map((r) => r.label?.toLowerCase()).join(', ')}` : 'Request'}</div>
            <div className="small muted">{o ? `Order ${o.id} · created ${time(o.created_at)}` : 'Sent to approval from the protocol check'}{s && ` · deliver by ${dayLabel(s.delivery_date)}`}</div>
          </div>
          <Pill tone={status.tone}>{status.label}</Pill>
        </div>

        {sel && (
          <div className="kv small">
            <div className="k">Option</div><div>{sel.candidate.lines[0].name}</div>
            <div className="k">Total incl. VAT</div><div className="strong">{eur(sel.result.quote.totals.gross_cents)}</div>
            <div className="k">Protocol check</div><div>{o?.gate.summary ?? sel.result.gate.summary}</div>
          </div>
        )}

        {pending && mode === 'SEPARATE' && (
          <>
            <Banner tone="info" title="Waiting for the procurement manager">
              In organisation mode the person who requests cannot approve. Mikko (procurement manager) has it in their queue.
            </Banner>
            <div className="row wrap">
              <Button disabled title="You requested this order, so you cannot approve it"><Lock size={15} /> Approve</Button>
              <span className="small" style={{ color: 'var(--bad)' }}>
                <span className="mono">403 SELF_APPROVAL_FORBIDDEN</span> · you requested this order, so someone else approves it.
              </span>
            </div>
            <div className="xs muted">Demo: switch account to Mikko (procurement manager) in the sidebar to continue.</div>
          </>
        )}
        {o?.status === 'PLACED' && <Banner tone="ok" title="Placed">{o.gate.summary}{o.placed_at && ` · ${time(o.placed_at)}`}</Banner>}
        {o?.status === 'REQUOTE_REQUIRED' && (
          <Banner tone="warn" title="Price changed after approval" action={<Button size="sm" onClick={() => navigate('/options')}>Choose again</Button>}>
            {o.requote_reason ?? 'The approved quote is no longer valid.'}
          </Banner>
        )}
        {flow.approvalResult === 'REJECTED' && <Banner tone="bad" title="The manager rejected this order" />}
        <div className="xs muted mono">GET /v1/orders/{'{id}'}</div>
      </article>
    </>
  );
}
