import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Package, RefreshCw } from 'lucide-react';
import { getOrder } from '../api/endpoints';
import { describeError } from '../api/client';
import { dayLabel, eur, num, ORDER_STATUS } from '../lib/format';
import { useFlow } from '../state/flow';
import { useSession } from '../state/session';
import { Banner, Button, Empty, PageHead, Pill, useToast } from '../components/ui';

export function OrdersPage() {
  const { flow, patch } = useFlow();
  const { mode, switchAccount } = useSession();
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
      if (fresh.id === o.id) patch({ order: fresh }); // the M1 fixture server returns ord_1 for any id
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setRefreshing(false);
    }
  };

  if (!o && !flow.sentToApproval) {
    return (
      <>
        <PageHead title="My orders" />
        <Empty icon={<Package size={22} />} title="No orders yet"
          action={<Button variant="primary" onClick={() => navigate(flow.request ? '/options' : '/request')}>{flow.request ? 'Back to options' : 'Start a request'}</Button>}>
          Orders you place or send for approval show up here.
        </Empty>
      </>
    );
  }

  const pending = !o || o.status === 'PENDING_BUYER_APPROVAL';
  const status = o ? ORDER_STATUS[o.status] : ORDER_STATUS.PENDING_BUYER_APPROVAL;
  const title = s ? `${num(s.quantity.value)} ${s.quantity.unit} ${(s.category_label ?? '').toLowerCase()}` : 'Order';
  const detail = [sel?.result.quote.lines[0]?.name, sel && eur(sel.result.quote.totals.gross_cents), s && `by ${dayLabel(s.delivery_date)}`].filter(Boolean).join(' · ');

  return (
    <>
      <PageHead title="My orders"
        actions={o && <Button variant="ghost" onClick={refresh} loading={refreshing}><RefreshCw size={15} /> Refresh</Button>} />

      <article className="card stack">
        <div className="row wrap">
          <div className="grow">
            <h3>{title}</h3>
            <div className="small muted">{detail}</div>
          </div>
          <Pill tone={status.tone}>{status.label}</Pill>
        </div>

        {pending && mode === 'SEPARATE' && (
          <div className="row wrap small text-2">
            <span className="grow">Waiting for Mikko, the procurement manager. You can’t approve your own order.</span>
            <Button size="sm" variant="ghost" onClick={() => { switchAccount('buyer_manager'); navigate('/approvals'); }}>Switch to Mikko (demo)</Button>
          </div>
        )}
        {o?.status === 'REQUOTE_REQUIRED' && (
          <Banner tone="warn" title="The price changed after approval" action={<Button size="sm" onClick={() => navigate('/options')}>Choose again</Button>}>
            {o.requote_reason ?? 'The approved price is no longer valid.'}
          </Banner>
        )}
        {flow.approvalResult === 'REJECTED' && <Banner tone="bad" title="The manager rejected this order" />}
      </article>
    </>
  );
}
