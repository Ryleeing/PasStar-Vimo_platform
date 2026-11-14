import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, CheckCircle2, Clock, X } from 'lucide-react';
import { eur, mmss, ORDER_STATUS, outcomeOf } from '../lib/format';
import { useFlow, type Selection } from '../state/flow';
import { useSession } from '../state/session';
import { Banner, Button, Pill } from './ui';

export function useCountdown(until: number) {
  const [left, setLeft] = useState(() => Math.round((until - Date.now()) / 1000));
  useEffect(() => {
    setLeft(Math.round((until - Date.now()) / 1000));
    const h = setInterval(() => setLeft(Math.round((until - Date.now()) / 1000)), 1000);
    return () => clearInterval(h);
  }, [until]);
  return left;
}

export function PriceTimer({ until }: { until: number }) {
  const left = useCountdown(until);
  const expired = left <= 0;
  return (
    <span className={`timer ${expired ? 'out' : left < 120 ? 'low' : ''}`} aria-live={left < 60 ? 'polite' : 'off'} title="Valio holds this price for 15 minutes">
      <Clock size={14} /> {expired ? 'Price expired' : `Price held ${mmss(left)}`}
    </span>
  );
}

/** Sticky bar shown once an option is chosen. One action: go to the review page. */
export function CheckoutBar({ selection, onClear }: { selection: Selection; onClear: () => void }) {
  const { flow } = useFlow();
  const { mode } = useSession();
  const navigate = useNavigate();
  const left = useCountdown(selection.expiresAt);
  const { quote, gate } = selection.result;
  const out = outcomeOf(gate.outcome, mode);
  const order = flow.order?.quote_id === quote.id ? flow.order : null;
  const expired = left <= 0;

  const action = order ? (
    order.status === 'PLACED' ? (
      <Pill tone="ok" icon={<CheckCircle2 />}>Ordered</Pill>
    ) : (
      <Button onClick={() => navigate(mode === 'SEPARATE' ? '/orders' : '/approvals')}>
        {mode === 'SEPARATE' ? 'View order' : 'Review and approve'}
      </Button>
    )
  ) : (
    <Button variant="primary" size="lg" disabled={expired || gate.outcome === 'REJECT'} onClick={() => navigate('/review')}>
      Review order <ArrowRight size={17} />
    </Button>
  );

  return (
    <section className="checkout" aria-label="Your choice">
      <div className="checkout-main">
        <div className="grow" style={{ minWidth: 180 }}>
          <div className="xs muted">Your choice</div>
          <div className="strong" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{quote.lines[0]?.name ?? selection.candidate.lines[0].name}</div>
        </div>
        <div className="total">{eur(quote.totals.gross_cents)}</div>
        {!order && gate.outcome !== 'AUTO' && <Pill tone={out.tone}>{out.label}</Pill>}
        {!order && <PriceTimer until={selection.expiresAt} />}
        {action}
        {!order && <Button variant="ghost" size="sm" className="icon-btn" aria-label="Clear selection" onClick={onClear}><X size={16} /></Button>}
      </div>

      {((order && order.status !== 'PLACED') || (expired && !order)) && (
        <div className="checkout-detail">
          {expired && !order && <Banner tone="warn" title="The price expired">Choose the option again to get a fresh price.</Banner>}
          {order && order.status === 'PENDING_BUYER_APPROVAL' && (
            <Banner tone="info" title={mode === 'SEPARATE' ? 'Sent to the procurement manager' : 'Waiting for your approval'}>
              Price and stock are checked again when it’s approved.
            </Banner>
          )}
          {order && (order.status === 'REJECTED' || order.status === 'REQUOTE_REQUIRED') && (
            <Banner tone="warn" title={ORDER_STATUS[order.status].label}>{order.requote_reason ?? order.gate.summary}</Banner>
          )}
        </div>
      )}
    </section>
  );
}
