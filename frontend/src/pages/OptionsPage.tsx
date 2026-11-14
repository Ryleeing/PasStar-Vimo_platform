import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Ban, ChevronDown, ChevronUp, LayoutGrid, MessageSquarePlus, PencilLine } from 'lucide-react';
import { selectCandidate } from '../api/endpoints';
import { describeError } from '../api/client';
import type { Candidate } from '../api/types';
import { dayLabel, eur, levelOf, num, outcomeOf } from '../lib/format';
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
        <PageHead eyebrow="Step 2 · Choose" title="Options" />
        <Empty icon={<LayoutGrid size={22} />} title="No request yet"
          action={<Button variant="primary" onClick={() => navigate('/request')}><MessageSquarePlus size={16} /> Start a request</Button>}>
          Describe what you need first. The agent then returns up to 8 ranked options from Valio Aimo’s catalogue.
        </Empty>
      </>
    );
  }

  const s = r.structured;
  const top = r.candidates.slice(0, 3);
  const more = r.candidates.slice(3);
  const all = [...r.candidates, ...(r.blocked ?? [])];
  const locked = !!flow.order && flow.order.status !== 'REQUOTE_REQUIRED';

  const choose = async (c: Candidate) => {
    if (locked) {
      toast('An order was already submitted for this request.', 'info');
      return;
    }
    setSelecting(c.id);
    try {
      const result = await selectCandidate(r.request_id, c.id);
      let expiresAt = Date.parse(result.quote.valid_until);
      // The M1 fixture server returns a fixed timestamp; if it is already past, assume clock skew and count 15 min from now.
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
      <PageHead
        eyebrow="Step 2 · Choose"
        title="Options for your request"
        desc={
          <span className="row wrap" style={{ gap: 6 }}>
            <Pill tone="neutral">{s.category_label ?? s.category}</Pill>
            {(s.requirements ?? []).map((q) => <Pill key={q.code} tone="neutral">{q.label ?? q.code}</Pill>)}
            <Pill tone="neutral">{num(s.quantity.value)} {s.quantity.unit}</Pill>
            <Pill tone="neutral">by {dayLabel(s.delivery_date)}</Pill>
          </span>
        }
        actions={<Button variant="ghost" onClick={() => navigate('/request')}><PencilLine size={15} /> New request</Button>}
      />

      <ChainView request={r} />

      {r.status === 'NO_CANDIDATES' || r.candidates.length === 0 ? (
        <div style={{ marginTop: 20 }}>
          <Banner tone="warn" title="No option meets every requirement">
            {r.no_candidate_reason ?? 'Nothing matched.'} Mark step ② “Excluded too much” above, or change the request. Requirements are never relaxed silently.
          </Banner>
        </div>
      ) : (
        <>
          <div className="section-title" id="options-grid">
            <h2>Top options</h2>
            <span className="small muted">Ranked by your profile weights · {r.candidates.length} of max 8</span>
          </div>
          <div className="grid three">
            {top.map((c, i) => (
              <CandidateCard key={c.id} c={c} all={all} featured={i === 0} mode={mode}
                selected={flow.selection?.candidate.id === c.id} loading={selecting === c.id} onSelect={() => choose(c)} />
            ))}
          </div>

          {more.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <Button variant="ghost" onClick={() => setShowMore((v) => !v)} aria-expanded={showMore}>
                {showMore ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                {showMore ? 'Hide lower-ranked options' : `Show ${more.length} more option${more.length > 1 ? 's' : ''}`}
              </Button>
              {showMore && (
                <div className="card fade-in" style={{ marginTop: 10, padding: 8 }}>
                  <div className="table-wrap">
                    <table className="table">
                      <thead><tr><th>#</th><th>Product</th><th>Total</th><th>Trust</th><th>If chosen</th><th>Why it ranks lower</th><th /></tr></thead>
                      <tbody>
                        {more.map((c) => {
                          const o = outcomeOf(c.outcome_preview, mode);
                          return (
                            <tr key={c.id}>
                              <td className="muted">{c.rank}</td>
                              <td className="strong">{c.lines[0].name}</td>
                              <td className="nowrap">{eur(c.totals?.gross_cents)}</td>
                              <td><Pill tone={levelOf(c.cards.trust.overall).tone}>{levelOf(c.cards.trust.overall).label}</Pill></td>
                              <td><Pill tone={o.tone}>{o.label}</Pill></td>
                              <td className="small text-2">{c.explanation}</td>
                              <td><Button size="sm" loading={selecting === c.id} onClick={() => choose(c)}>{flow.selection?.candidate.id === c.id ? 'Selected' : 'Choose'}</Button></td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}
        </>
      )}

      {(r.blocked?.length ?? 0) > 0 && (
        <>
          <div className="section-title">
            <h2>Does not meet your requirements</h2>
            <span className="small muted">Shown so you know why. These cannot be chosen.</span>
          </div>
          <div className="stack sm">
            {r.blocked!.map((b) => (
              <div key={b.id} className="blocked-row" aria-disabled="true">
                <Ban size={18} color="var(--bad)" style={{ flex: 'none', marginTop: 2 }} aria-hidden />
                <div className="grow stack sm">
                  <span className="name">{b.lines[0].name}</span>
                  {(b.violations ?? []).map((v, i) => <span key={i} className="small" style={{ color: 'var(--bad)' }}>{v.detail}</span>)}
                  <span className="small text-2">{b.explanation}</span>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {flow.sentToApproval && !flow.selection && (
        <div style={{ marginTop: 20 }}>
          <Banner tone="info" title={mode === 'SEPARATE' ? 'Sent to the procurement manager' : 'Waiting for your approval'}>
            You sent this request to human approval from the protocol check.
          </Banner>
        </div>
      )}

      {flow.selection && <CheckoutBar selection={flow.selection} onClear={() => patch({ selection: null })} />}
    </>
  );
}
