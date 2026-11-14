// Approver's review: the same order details the requester saw, who asked and why it stopped,
// then "Approve and order" as the final step. Rejecting happens on the approvals list.
import { useEffect, useState } from 'react';
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { decideApproval, listApprovals, listFaults, listProfiles, setFault } from '../api/endpoints';
import { ApiError, describeError } from '../api/client';
import { accountByUserId } from '../config/accounts';
import { eur } from '../lib/format';
import { useFlow } from '../state/flow';
import { useSession } from '../state/session';
import { OrderDetails, OrderSummary } from '../components/OrderReview';
import { Banner, Button, ErrorState, PageHead, Skeleton, Switch, useAsync, useToast } from '../components/ui';

export function ApproveReviewPage() {
  const [params] = useSearchParams();
  const id = params.get('id');
  const { account, workspace, mode } = useSession();
  const { flow, patch } = useFlow();
  const navigate = useNavigate();
  const toast = useToast();
  const list = useAsync(() => listApprovals('BUYER'), [account.key]);
  const profiles = useAsync(() => listProfiles(workspace.buyerId!), [workspace.buyerId]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [priceChange, setPriceChange] = useState(false);

  useEffect(() => {
    listFaults().then((fs) => setPriceChange(!!fs.find((f) => f.name === 'PRICE_CHANGE')?.enabled)).catch(() => undefined);
  }, []);

  if (list.loading) return <Skeleton h={420} />;
  if (list.error) return <ErrorState error={list.error} onRetry={list.reload} />;
  const a = list.data?.find((x) => x.id === id);
  // Unknown, already decided, or nothing to order → back to the list.
  if (!a || flow.decisions[a.id] || a.status !== 'PENDING' || !(a.quote || a.candidate)) return <Navigate to="/approvals" replace />;

  const line = a.quote?.lines[0] ?? a.candidate!.lines[0];
  const totals = a.quote?.totals ?? a.candidate!.totals!;
  const deliveryDate = a.quote?.delivery_date ?? a.candidate!.delivery_date;
  const requester = accountByUserId(a.requested_by);
  const byMe = a.requested_by === account.userId;

  const toggleFault = async (v: boolean) => {
    setPriceChange(v);
    try {
      await setFault('PRICE_CHANGE', v);
    } catch (e) {
      setPriceChange(!v);
      toast(describeError(e), 'bad');
    }
  };

  const approve = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await decideApproval(a.id, { decision: 'APPROVE', note: null, changes: {} });
      const o = res.order;
      const requote = o?.status === 'REQUOTE_REQUIRED';
      patch({ decisions: { ...flow.decisions, [a.id]: res }, order: o ?? flow.order, approvalResult: requote ? 'REQUOTE' : 'APPROVED' });
      // Placed → the same confirmation every order gets. Price changed → back to the list, which explains it.
      navigate(o?.status === 'PLACED' ? '/ordered' : '/approvals');
    } catch (e) {
      if (e instanceof ApiError) setError(e);
      else toast(describeError(e), 'bad');
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHead title="Review and approve"
        desc={<>{byMe ? 'You asked' : `${requester ? requester.name : a.requested_by} asked`}{a.raw_text ? <>: “{a.raw_text}”</> : null}</>}
        actions={<Button variant="ghost" onClick={() => navigate('/approvals')}><ArrowLeft size={16} /> Back</Button>} />

      <div className="review">
        <div className="stack lg">
          <Banner tone="warn" title="Why it needs approval">{a.reasons.join(' ')}</Banner>
          <OrderDetails line={line} candidate={a.candidate} deliveryDate={deliveryDate} address={profiles.data?.[0]?.body.defaults?.delivery_address} />
        </div>
        <OrderSummary
          totals={totals}
          note={byMe && mode === 'SELF' ? 'Recorded as self-approved. Price and stock are checked again before ordering.' : 'Price and stock are checked again before ordering.'}
          action={
            <>
              {error && <Banner tone="bad" title="Can’t approve this">{describeError(error)}</Banner>}
              <Button variant="primary" size="lg" block loading={busy} onClick={approve}>Approve and order · {eur(totals.gross_cents)}</Button>
            </>
          }
          footer={
            <label className="row small muted demo-toggle">
              <Switch checked={priceChange} onChange={toggleFault} label="Demo: price changes before the re-check" />
              Demo: price changes before the re-check
            </label>
          }
        />
      </div>
    </>
  );
}
