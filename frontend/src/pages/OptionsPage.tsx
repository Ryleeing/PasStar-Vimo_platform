import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Ban, ChevronDown, ChevronUp, CircleX, PencilLine } from 'lucide-react';
import { selectCandidate } from '../api/endpoints';
import { describeError } from '../api/client';
import type { Candidate } from '../api/types';
import { dayLabel, eur, num } from '../lib/format';
import { useFlow } from '../state/flow';
import { useSession } from '../state/session';
import { ChainView } from '../components/ChainView';
import { OptionRow } from '../components/OptionRow';
import { CheckoutBar } from '../components/CheckoutBar';
import { Mascot } from '../components/Mascot';
import { Banner, Button, Empty, PageHead, useToast } from '../components/ui';

/** "Milk type: Semi-skimmed" → ["semi-skimmed"]: the answer to the AI's question, if any. */
const clarifiedOf = (notes?: string | null) => (notes?.includes(': ') ? [notes.split(': ').pop()!.toLowerCase()] : []);

/** "€998.80–€1,044.20" for a set of options. */
const priceRange = (cs: Candidate[]) => {
  const p = cs.map((c) => c.totals?.gross_cents).filter((v): v is number => v != null);
  if (!p.length) return '';
  const lo = Math.min(...p), hi = Math.max(...p);
  return lo === hi ? eur(lo) : `${eur(lo)}–${eur(hi)}`;
};

const newKey = () => (typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `idem-${Date.now()}-${Math.random().toString(16).slice(2)}`);

export function OptionsPage() {
  const { flow, patch } = useFlow();
  const { mode } = useSession();
  const navigate = useNavigate();
  const toast = useToast();
  const [showMore, setShowMore] = useState(false);
  const [showExcluded, setShowExcluded] = useState(false);
  const [selecting, setSelecting] = useState<string | null>(null);
  const r = flow.request;
  const run = flow.run;

  if (!r && !run) {
    return (
      <>
        <PageHead title="Options" />
        <Empty icon={<Mascot size={92} />} title="No request yet"
          action={<Button variant="primary" onClick={() => navigate('/request')}>Start a request</Button>}>
          Tell us what you need and you’ll get up to 8 ranked options here.
        </Empty>
      </>
    );
  }

  const s = r?.structured ?? flow.panel;
  const summary = s ? [s.category_label ?? s.category, ...clarifiedOf(s.notes), ...(s.requirements ?? []).map((q) => (q.label ?? q.code).toLowerCase()),
    `${num(s.quantity.value)} ${s.quantity.unit}`, `by ${dayLabel(s.delivery_date)}`].join(' · ') : undefined;

  // While the agent works, only the steps are shown; the options follow once they're done.
  // ChainView stays mounted across both so it can fold itself away when the run ends.
  const working = !!run || !r;
  const top = r?.candidates.slice(0, 3) ?? [];
  const more = r?.candidates.slice(3) ?? [];
  const all = r ? [...r.candidates, ...(r.blocked ?? [])] : [];

  const choose = async (c: Candidate) => {
    if (flow.order && flow.order.status !== 'REQUOTE_REQUIRED') {
      toast('This request already has an order.', 'info');
      return;
    }
    if (!r) return;
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
      <PageHead title={working && !run?.error ? 'Finding options' : 'Choose an option'} desc={summary}
        actions={!working && <Button className="edit-req" onClick={() => navigate('/request')}><PencilLine size={16} /> Edit request</Button>} />

      <div className="run-slot">
        <ChainView request={r} />
      </div>

      {r && !working && (<>
      {r.candidates.length === 0 ? (
        <Banner tone="warn" title="Nothing meets every requirement">
          {r.no_candidate_reason ?? 'No product matched.'} Open the steps above and adjust “Filter products”, or edit the request.
        </Banner>
      ) : (
        <>
          <ol className="opts reveal" id="options-grid" aria-label="Options, best match first">
            {(showMore ? r.candidates : top).map((c, i) => (
              <OptionRow key={c.id} c={c} position={i + 1} best={r.candidates[0]} all={all} mode={mode}
                onChooseAlt={(id) => { const alt = all.find((x) => x.id === id); if (alt?.selectable) choose(alt); }}
                selected={flow.selection?.candidate.id === c.id} loading={selecting === c.id} onSelect={() => choose(c)} />
            ))}
          </ol>

          {more.length > 0 && (
            <button type="button" className="opts-more" onClick={() => setShowMore((v) => !v)} aria-expanded={showMore} aria-controls="options-grid">
              {showMore ? <ChevronUp size={17} aria-hidden /> : <ChevronDown size={17} aria-hidden />}
              {showMore ? 'Show fewer options' : `Show ${more.length} more option${more.length > 1 ? 's' : ''}`}
              {!showMore && <span className="opts-more-hint">{priceRange(more)}</span>}
            </button>
          )}
        </>
      )}

      {(r.blocked?.length ?? 0) > 0 && (
        <section className={`excl ${showExcluded ? 'open' : ''}`}>
          <button type="button" className="excl-head" onClick={() => setShowExcluded((v) => !v)} aria-expanded={showExcluded} aria-controls="excluded-list">
            <Ban size={18} className="excl-icon" aria-hidden />
            <span className="grow">
              <span className="excl-title">{r.blocked!.length} product{r.blocked!.length > 1 ? 's' : ''} left out</span>
              <span className="excl-sub">They don’t meet your hard requirements, so they can’t be ordered here.</span>
            </span>
            <span className="excl-toggle">{showExcluded ? 'Hide' : 'See why'}<ChevronDown size={16} className="chev" aria-hidden /></span>
          </button>
          {showExcluded && (
            <ul id="excluded-list" className="excl-list">
              {r.blocked!.map((b) => (
                <li key={b.id}>
                  <div className="excl-name">{b.lines[0].name}</div>
                  {(b.violations ?? []).map((v, i) => (
                    <div key={i} className="excl-why"><CircleX size={15} aria-hidden />{v.detail}</div>
                  ))}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {flow.sentToApproval && !flow.selection && (
        <div style={{ marginTop: 20 }}>
          <Banner tone="info" title={mode === 'SEPARATE' ? 'Sent to the procurement manager' : 'Waiting for your approval'} />
        </div>
      )}

      </>)}

      {flow.selection && <CheckoutBar selection={flow.selection} onClear={() => patch({ selection: null })} />}
    </>
  );
}
