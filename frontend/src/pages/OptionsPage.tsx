import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronDown, ChevronUp, LayoutGrid, PencilLine } from 'lucide-react';
import { selectCandidate } from '../api/endpoints';
import { describeError } from '../api/client';
import type { Candidate } from '../api/types';
import { dayLabel, eur, num, outcomeOf } from '../lib/format';
import { useFlow } from '../state/flow';
import { useSession } from '../state/session';
import { ChainView } from '../components/ChainView';
import { CandidateCard } from '../components/CandidateCard';
import { CheckoutBar } from '../components/CheckoutBar';
import { Banner, Button, Empty, PageHead, Pill, useToast } from '../components/ui';

const newKey = () => (typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `idem-${Date.now()}-${Math.random().toString(16).slice(2)}`);

export function OptionsPage() {
  const { flow, patch } = useFlow();
  const { mode } = useSession();
  const navigate = useNavigate();
  const toast = useToast();
  const [showMore, setShowMore] = useState(false);
  const [selecting, setSelecting] = useState<string | null>(null);
  const r = flow.request;

  if (!r) {
    return (
      <>
        <PageHead title="Options" />
        <Empty icon={<LayoutGrid size={22} />} title="No request yet"
          action={<Button variant="primary" onClick={() => navigate('/request')}>Start a request</Button>}>
          Tell us what you need and you’ll get up to 8 ranked options here.
        </Empty>
      </>
    );
  }

  const s = r.structured;
  const top = r.candidates.slice(0, 3);
  const more = r.candidates.slice(3);
  const all = [...r.candidates, ...(r.blocked ?? [])];
  const summary = [s.category_label ?? s.category, ...(s.requirements ?? []).map((q) => (q.label ?? q.code).toLowerCase()),
    `${num(s.quantity.value)} ${s.quantity.unit}`, `by ${dayLabel(s.delivery_date)}`].join(' · ');

  const choose = async (c: Candidate) => {
    if (flow.order && flow.order.status !== 'REQUOTE_REQUIRED') {
      toast('This request already has an order.', 'info');
      return;
    }
    setSelecting(c.id);
    try {
      const result = await selectCandidate(r.request_id, c.id);
      let expiresAt = Date.parse(result.quote.valid_until);
      // The M1 fixture server returns a fixed timestamp; if it is already past, count 15 min from now.
      if (!(expiresAt > Date.now())) expiresAt = Date.now() + 15 * 60_000;
      patch({ selection: { candidate: c, result, expiresAt, idempotencyKey: newKey() }, order: null, sentToApproval: false, approvalResult: null });
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setSelecting(null);
    }
  };

  return (
    <>
      <PageHead title="Choose an option" desc={summary}
        actions={<Button variant="ghost" onClick={() => navigate('/request')}><PencilLine size={15} /> Edit request</Button>} />

      {r.candidates.length === 0 ? (
        <Banner tone="warn" title="Nothing meets every requirement">
          {r.no_candidate_reason ?? 'No product matched.'} Mark “Filter products” below as not right, or edit the request.
        </Banner>
      ) : (
        <>
          <div className="grid three" id="options-grid">
            {top.map((c, i) => (
              <CandidateCard key={c.id} c={c} all={all} featured={i === 0} mode={mode}
                selected={flow.selection?.candidate.id === c.id} loading={selecting === c.id} onSelect={() => choose(c)} />
            ))}
          </div>

          {more.length > 0 && (
            <div style={{ marginTop: 14 }}>
              <Button variant="ghost" onClick={() => setShowMore((v) => !v)} aria-expanded={showMore}>
                {showMore ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                {showMore ? 'Fewer options' : `${more.length} more option${more.length > 1 ? 's' : ''}`}
              </Button>
              {showMore && (
                <div className="card" style={{ marginTop: 8, padding: 8 }}>
                  <div className="table-wrap">
                    <table className="table">
                      <tbody>
                        {more.map((c) => (
                          <tr key={c.id}>
                            <td><div className="strong">{c.lines[0].name}</div><div className="small muted">{c.explanation}</div></td>
                            <td className="nowrap strong">{eur(c.totals?.gross_cents)}</td>
                            <td>{c.outcome_preview !== 'AUTO' && <Pill tone={outcomeOf(c.outcome_preview, mode).tone}>{outcomeOf(c.outcome_preview, mode).label}</Pill>}</td>
                            <td style={{ textAlign: 'right' }}>
                              <Button size="sm" loading={selecting === c.id} onClick={() => choose(c)}>{flow.selection?.candidate.id === c.id ? 'Selected' : 'Choose'}</Button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}
        </>
      )}

      <div style={{ marginTop: 28 }}>
        <ChainView request={r} />
      </div>

      {(r.blocked?.length ?? 0) > 0 && (
        <details className="details excluded" style={{ marginTop: 16 }}>
          <summary>{r.blocked!.length} excluded by your requirements <ChevronDown size={14} className="chev" /></summary>
          <div className="details-body">
            {r.blocked!.map((b) => (
              <div key={b.id} className="row top" style={{ gap: 10 }}>
                <span className="dot tone-dot-bad" style={{ marginTop: 7 }} aria-hidden />
                <div><span className="strong">{b.lines[0].name}</span><div className="small muted">{(b.violations ?? []).map((v) => v.detail).join('; ')}</div></div>
              </div>
            ))}
          </div>
        </details>
      )}

      {flow.sentToApproval && !flow.selection && (
        <div style={{ marginTop: 20 }}>
          <Banner tone="info" title={mode === 'SEPARATE' ? 'Sent to the procurement manager' : 'Waiting for your approval'} />
        </div>
      )}

      {flow.selection && <CheckoutBar selection={flow.selection} onClear={() => patch({ selection: null })} />}
    </>
  );
}
