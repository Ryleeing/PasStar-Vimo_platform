// Confirmation after any placed order: an auto order, an approved one, or the owner approving their own.
// One message, the few facts people check, and where to find the order later.
import { Link, Navigate, useNavigate } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';
import { PAGES_BY_ROLE } from '../config/accounts';
import { dayLabel, eur, num } from '../lib/format';
import { useFlow } from '../state/flow';
import { useSession } from '../state/session';
import { Button } from '../components/ui';
import { OrderedIllustration } from '../components/Illustration';

export function OrderedPage() {
  const { flow, reset } = useFlow();
  const { account } = useSession();
  const navigate = useNavigate();
  const o = flow.order;
  if (!o || o.status !== 'PLACED') return <Navigate to={PAGES_BY_ROLE[account.key][0].path} replace />;

  // The quote comes from the requester's own selection, or from the approval that placed it.
  const decision = Object.values(flow.decisions).find((d) => d.order?.id === o.id);
  const quote = flow.selection?.result.quote ?? decision?.approval.quote ?? null;
  const line = quote?.lines[0] ?? decision?.approval.candidate?.lines[0];
  const total = quote?.totals.gross_cents ?? decision?.approval.candidate?.totals?.gross_cents;
  const s = flow.request?.structured ?? decision?.approval.request ?? null;
  const deliveryDate = quote?.delivery_date ?? s?.delivery_date;
  const canSeeOrders = PAGES_BY_ROLE[account.key].some((p) => p.key === 'orders');
  const what = s
    ? `${num(s.quantity.value)} ${s.quantity.unit} of ${(s.requirements ?? []).map((r) => (r.label ?? '').toLowerCase()).filter(Boolean).join(', ')} ${(s.category_label ?? '').toLowerCase()}`.replace(/\s+/g, ' ').trim()
    : 'the order';

  return (
    <div className="ordered">
      <OrderedIllustration />
      <h1>{decision ? 'Approved and ordered' : 'Order placed'}</h1>
      <p className="lead">
        Valio Aimo will deliver {what}{deliveryDate ? <> by <b className="nowrap">{dayLabel(deliveryDate)}</b></> : null}.
      </p>

      <dl className="receipt">
        <div><dt>Order number</dt><dd className="mono">{o.id}</dd></div>
        {line && <div><dt>Product</dt><dd>{line.name}</dd></div>}
        {total != null && <div><dt>Total</dt><dd><b>{eur(total)}</b> <span className="muted">incl. VAT</span></dd></div>}
        {decision && <div><dt>Approval</dt><dd>Approved by you</dd></div>}
      </dl>

      {canSeeOrders
        ? <p className="muted">You can track this order anytime in <Link to="/orders">My orders</Link>.</p>
        : <p className="muted">The requester can track it in My orders. It’s also in the <Link to="/audit">Activity log</Link>.</p>}

      <div className="row wrap" style={{ justifyContent: 'center' }}>
        {canSeeOrders ? (
          <>
            <Button variant="primary" size="lg" onClick={() => navigate('/orders')}>View my orders <ArrowRight size={17} /></Button>
            <Button variant="ghost" size="lg" onClick={() => { reset(); navigate('/request'); }}>New request</Button>
          </>
        ) : (
          <Button variant="primary" size="lg" onClick={() => navigate('/approvals')}>Back to approvals <ArrowRight size={17} /></Button>
        )}
      </div>
    </div>
  );
}
