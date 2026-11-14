// My orders: every order of this buyer, newest first, whoever placed it: someone on these screens or one of the
// buyer's agents (ChatGPT, Claude, the ERP). Read from PasStar, refreshed every few seconds while the page is open
// and when the tab comes back into view, so an order an agent just placed shows up on its own.
import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Bot, Building2, Package, RefreshCw, Sparkles, User } from 'lucide-react';
import { listOrders } from '../api/endpoints';
import type { OrderSummary } from '../api/types';
import { dayLabel, eur, num, ORDER_STATUS } from '../lib/format';
import { useFlow } from '../state/flow';
import { useSession } from '../state/session';
import { Banner, Button, Empty, ErrorState, PageHead, Pill, Skeleton, useAsync } from '../components/ui';

const REFRESH_MS = 10_000;
const AGENT_ICON: Record<string, JSX.Element> = {
  CHATGPT: <Sparkles size={14} aria-hidden />, CLAUDE: <Bot size={14} aria-hidden />, ERP: <Building2 size={14} aria-hidden />,
};
const AGENT_LABEL: Record<string, string> = { CHATGPT: 'ChatGPT agent', CLAUDE: 'Claude agent', ERP: 'ERP', UNKNOWN: 'agent' };

const when = (iso: string) => new Date(iso).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

export function OrdersPage() {
  const { flow } = useFlow();
  const { account, mode, switchAccount } = useSession();
  const navigate = useNavigate();
  const list = useAsync(listOrders, [account.key, flow.order?.status, flow.approvalResult]);

  // Keep the list current: agents place orders outside these screens.
  useEffect(() => {
    const t = setInterval(() => list.reload(), REFRESH_MS);
    const onVisible = () => document.visibilityState === 'visible' && list.reload();
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearInterval(t);
      document.removeEventListener('visibilitychange', onVisible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account.key]);

  const rows = list.data ?? [];
  return (
    <>
      <PageHead title="My orders"
        desc="Every order for your kitchen, whether you placed it here or one of your AI agents did."
        actions={<Button variant="ghost" onClick={() => list.reload()} loading={list.loading && !!list.data}><RefreshCw size={15} /> Refresh</Button>} />

      {flow.sentToApproval && !flow.order && (
        <div style={{ marginBottom: 16 }}>
          <Banner tone="info" title="Sent for approval">The order is created once the approver decides.</Banner>
        </div>
      )}

      {list.loading && !list.data ? <Skeleton h={260} /> : list.error && !list.data ? <ErrorState error={list.error} onRetry={list.reload} /> : !rows.length ? (
        <Empty icon={<Package size={22} />} title="No orders yet"
          action={<Button variant="primary" onClick={() => navigate(flow.request ? '/options' : '/request')}>{flow.request ? 'Back to options' : 'Start a request'}</Button>}>
          Orders placed here or by your agents show up here.
        </Empty>
      ) : (
        <ol className="order-list" aria-label="Orders, newest first">
          {rows.map((r) => <OrderRow key={r.order.id} r={r} me={account.userId} mode={mode}
            onApprover={() => { switchAccount('buyer_manager'); navigate('/approvals'); }}
            onApprove={() => navigate('/approvals')} onChooseAgain={() => navigate('/options')} />)}
        </ol>
      )}
    </>
  );
}

function OrderRow({ r, me, mode, onApprover, onApprove, onChooseAgain }: {
  r: OrderSummary; me: string; mode: 'SEPARATE' | 'SELF'; onApprover: () => void; onApprove: () => void; onChooseAgain: () => void;
}) {
  const o = r.order;
  const status = ORDER_STATUS[o.status] ?? { label: o.status, tone: 'neutral' as const };
  const by = r.placed_by;
  const pending = o.status === 'PENDING_BUYER_APPROVAL';
  return (
    <li className="card order-row">
      <div className="order-main">
        <div className="row wrap" style={{ gap: 8 }}>
          <h3 className="grow">{num(r.quantity.value)} {r.quantity.unit} · {r.product}</h3>
          <Pill tone={status.tone}>{status.label}</Pill>
        </div>
        <div className="small muted">
          {eur(r.total_gross_cents)} incl. VAT · delivery {dayLabel(r.delivery_date)} · ordered {when(o.created_at)} · <span className="mono">{o.id}</span>
        </div>
        <div className={`placed-by ${by.kind === 'AGENT' ? 'agent' : ''}`}>
          {by.kind === 'AGENT' ? (AGENT_ICON[by.agent_kind ?? ''] ?? <Bot size={14} aria-hidden />) : <User size={14} aria-hidden />}
          {by.kind === 'AGENT'
            ? <>Placed by <b>{by.name}</b> ({AGENT_LABEL[by.agent_kind ?? 'UNKNOWN'] ?? 'agent'})</>
            : <>Placed by <b>{by.id === me ? 'you' : by.name}</b></>}
        </div>
      </div>

      {pending && mode === 'SEPARATE' && (
        <div className="row wrap small text-2">
          <span className="grow">Waiting for Mikko, the procurement manager.{by.kind === 'PERSON' && by.id === me && ' You can’t approve your own order.'}</span>
          {me !== 'usr_manager' && <Button size="sm" variant="ghost" onClick={onApprover}>Switch to Mikko (demo)</Button>}
          {me === 'usr_manager' && <Button size="sm" onClick={onApprove}>Review it</Button>}
        </div>
      )}
      {pending && mode === 'SELF' && (
        <div className="row wrap small text-2">
          <span className="grow">Waiting for your approval.</span>
          <Button size="sm" onClick={onApprove}>Review it</Button>
        </div>
      )}
      {o.status === 'REQUOTE_REQUIRED' && (
        <Banner tone="warn" title="The price changed after approval" action={<Button size="sm" onClick={onChooseAgain}>Choose again</Button>}>
          {o.requote_reason ?? 'The approved price is no longer valid.'}
        </Banner>
      )}
    </li>
  );
}
