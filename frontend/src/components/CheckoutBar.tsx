import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { CheckCircle2, ChevronDown, ChevronUp, Clock, FileSignature, X } from 'lucide-react';
import { placeOrder, verifyQuote } from '../api/endpoints';
import { describeError } from '../api/client';
import { eur, mmss, ORDER_STATUS, outcomeOf } from '../lib/format';
import { useFlow, type Selection } from '../state/flow';
import { useSession } from '../state/session';
import { Banner, Button, Pill, useToast } from './ui';

function useCountdown(until: number) {
  const [left, setLeft] = useState(() => Math.round((until - Date.now()) / 1000));
  useEffect(() => {
    setLeft(Math.round((until - Date.now()) / 1000));
    const h = setInterval(() => setLeft(Math.round((until - Date.now()) / 1000)), 1000);
    return () => clearInterval(h);
  }, [until]);
  return left;
}

/** Sticky "signed quote" bar shown once an option is chosen: total, timer, protocol verdict, one primary action. */
export function CheckoutBar({ selection, onClear }: { selection: Selection; onClear: () => void }) {
  const { flow, patch } = useFlow();
  const { mode, workspace } = useSession();
  const navigate = useNavigate();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [placing, setPlacing] = useState(false);
  const left = useCountdown(selection.expiresAt);
  const { quote, gate } = selection.result;
  const out = outcomeOf(gate.outcome, mode);
  const order = flow.order?.quote_id === quote.id ? flow.order : null;
  const expired = left <= 0;

  const place = async () => {
    setPlacing(true);
    try {
      // Same Idempotency-Key on every retry of this quote → the backend never creates two orders.
      const o = await placeOrder(quote.id, quote.protocol_id ?? workspace.protocolId!, selection.idempotencyKey);
      patch({ order: o });
      if (o.status === 'PLACED') toast(`Order ${o.id} placed`, 'ok');
      else if (o.status === 'PENDING_BUYER_APPROVAL') {
        toast(mode === 'SEPARATE' ? 'Submitted. The procurement manager has been asked to approve.' : 'Added to your approval list', 'info');
        if (mode === 'SELF') navigate('/approvals');
      } else toast(ORDER_STATUS[o.status]?.label ?? o.status, 'warn');
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setPlacing(false);
    }
  };

  const verify = async () => {
    try {
      const v = await verifyQuote(quote.id);
      toast(v.signature_valid && !v.expired ? `Signature valid (key ${v.key_id}), not expired` : v.expired ? 'Signature valid, but the quote has expired' : 'Signature is NOT valid', v.signature_valid && !v.expired ? 'ok' : 'warn');
    } catch (e) {
      toast(describeError(e), 'bad');
    }
  };

  const action = order ? (
    order.status === 'PLACED' ? (
      <Pill tone="ok" icon={<CheckCircle2 />}>Order {order.id} placed</Pill>
    ) : (
      <Button onClick={() => navigate(mode === 'SEPARATE' ? '/orders' : '/approvals')}>
        {mode === 'SEPARATE' ? 'Track in My orders' : 'Review and approve'}
      </Button>
    )
  ) : (
    <Button variant="primary" size="lg" loading={placing} disabled={expired || gate.outcome === 'REJECT'} onClick={place}>
      {gate.outcome === 'AUTO' ? 'Place order' : mode === 'SEPARATE' ? 'Submit for approval' : 'Request approval'}
    </Button>
  );

  return (
    <section className="checkout" aria-label="Signed quote">
      <div className="checkout-main">
        <div className="icon-tile"><FileSignature size={18} /></div>
        <div className="grow" style={{ minWidth: 180 }}>
          <div className="xs muted">Signed quote {quote.id}</div>
          <div className="strong" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{quote.lines[0]?.name ?? selection.candidate.lines[0].name}</div>
        </div>
        <div>
          <div className="xs muted">Total incl. VAT</div>
          <div className="total">{eur(quote.totals.gross_cents)}</div>
        </div>
        {!order && <Pill tone={out.tone}>{out.label}</Pill>}
        {!order && (
          <span className={`timer ${expired ? 'out' : left < 120 ? 'low' : ''}`} aria-live={left < 60 ? 'polite' : 'off'} title="The quote is valid for 15 minutes">
            <Clock size={14} /> {expired ? 'Expired' : mmss(left)}
          </span>
        )}
        {action}
        <Button variant="ghost" size="sm" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label="Quote details">
          {open ? <ChevronDown size={16} /> : <ChevronUp size={16} />} Details
        </Button>
        {!order && <Button variant="ghost" size="sm" className="icon-btn" aria-label="Clear selection" onClick={onClear}><X size={16} /></Button>}
      </div>

      {(open || (order && order.status !== 'PLACED') || (expired && !order)) && (
        <div className="checkout-detail">
          {open && (
            <>
              <div className="kv small">
                <div className="k">Net</div><div>{eur(quote.totals.net_cents)}</div>
                <div className="k">VAT</div><div>{eur(quote.totals.vat_cents)}</div>
                <div className="k">Deposits</div><div>{eur(quote.totals.deposit_cents)}</div>
                <div className="k strong">Total</div><div className="strong">{eur(quote.totals.gross_cents)}</div>
              </div>
              <div className="stack sm small">
                <div className="strong">Protocol check</div>
                <div className="text-2">{gate.summary}</div>
                {gate.rules_hit.map((r) => (
                  <div key={r.rule_id} className="row" style={{ gap: 8 }}><span className="mono">{r.rule_id}</span><span className="muted">{r.description}</span></div>
                ))}
                {gate.outcome !== 'AUTO' && (
                  <div className="text-2">{mode === 'SEPARATE' ? 'The agent stops here. The procurement manager approves; you cannot approve your own order.' : 'The agent cannot order this alone. You approve it as a separate, logged step.'}</div>
                )}
                <div className="row xs muted" style={{ gap: 6 }}>
                  Signature <span className="mono">{quote.jws.slice(0, 26)}…</span>
                  <button className="btn ghost sm" onClick={verify}>Verify</button>
                </div>
              </div>
            </>
          )}
          {expired && !order && (
            <Banner tone="warn" title="This quote expired">Choose the option again to get a fresh signed quote.</Banner>
          )}
          {order && order.status === 'PENDING_BUYER_APPROVAL' && (
            <Banner tone="info" title={mode === 'SEPARATE' ? 'Waiting for the procurement manager' : 'Waiting for your approval'}>
              Approval {order.approval_id} was created. The price and stock are re-checked when it is approved.
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
