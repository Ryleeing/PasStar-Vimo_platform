// Requester's review: everything that will be ordered, then a single "place" action.
import { useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { listProfiles, placeOrder, verifyQuote } from '../api/endpoints';
import { describeError } from '../api/client';
import { eur, ORDER_STATUS } from '../lib/format';
import { useFlow } from '../state/flow';
import { useSession } from '../state/session';
import { PriceTimer, useCountdown } from '../components/CheckoutBar';
import { OrderDetails, OrderSummary } from '../components/OrderReview';
import { Banner, Button, PageHead, useAsync, useToast } from '../components/ui';

export function ReviewPage() {
  const { flow } = useFlow();
  const sel = flow.selection;
  if (!sel || (flow.order && flow.order.quote_id === sel.result.quote.id)) return <Navigate to="/options" replace />;
  return <Review />;
}

function Review() {
  const { flow, patch } = useFlow();
  const { mode, workspace } = useSession();
  const navigate = useNavigate();
  const toast = useToast();
  const [placing, setPlacing] = useState(false);
  const profiles = useAsync(() => listProfiles(workspace.buyerId!), [workspace.buyerId]);

  const sel = flow.selection!;
  const { quote, gate } = sel.result;
  const expired = useCountdown(sel.expiresAt) <= 0;
  const needsApproval = gate.outcome !== 'AUTO';

  const place = async () => {
    setPlacing(true);
    try {
      // Same Idempotency-Key on every retry of this quote → never two orders.
      const o = await placeOrder(quote.id, quote.protocol_id ?? workspace.protocolId!, sel.idempotencyKey);
      patch({ order: o });
      if (o.status === 'PLACED') navigate('/ordered');
      else if (o.status === 'PENDING_BUYER_APPROVAL') {
        toast(mode === 'SEPARATE' ? 'Sent to the procurement manager for approval' : 'Added to your approval list', 'info');
        navigate(mode === 'SEPARATE' ? '/orders' : '/approvals');
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
      toast(v.signature_valid && !v.expired ? 'Signed by Valio and still valid' : v.expired ? 'Signed by Valio, but expired' : 'Signature is not valid', v.signature_valid && !v.expired ? 'ok' : 'warn');
    } catch (e) {
      toast(describeError(e), 'bad');
    }
  };

  return (
    <>
      <PageHead title="Review your order" desc="Check the details. Nothing is ordered until you confirm."
        actions={<Button variant="ghost" onClick={() => navigate('/options')}><ArrowLeft size={16} /> Change option</Button>} />
      <div className="review">
        <OrderDetails line={quote.lines[0] ?? sel.candidate.lines[0]} candidate={sel.candidate} deliveryDate={quote.delivery_date}
          address={profiles.data?.[0]?.body.defaults?.delivery_address} />
        <OrderSummary
          totals={quote.totals}
          note={needsApproval ? (mode === 'SEPARATE'
            ? 'This is above your auto-order limit. The procurement manager reviews it before it’s ordered.'
            : 'This is above your auto-order limit. You’ll review and approve it in the next step.') : undefined}
          action={expired
            ? <Banner tone="warn" title="The price expired" action={<Button size="sm" onClick={() => navigate('/options')}>Choose again</Button>} />
            : (
              <Button variant="primary" size="lg" block loading={placing} disabled={gate.outcome === 'REJECT'} onClick={place}>
                {needsApproval ? (mode === 'SEPARATE' ? 'Send for approval' : 'Continue to approval') : `Place order · ${eur(quote.totals.gross_cents)}`}
              </Button>
            )}
          footer={<div className="row between"><PriceTimer until={sel.expiresAt} /><button className="btn ghost sm" onClick={verify}>Verify price</button></div>}
        />
      </div>
    </>
  );
}
