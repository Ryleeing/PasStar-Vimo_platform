import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronDown, ChevronUp, PencilLine } from 'lucide-react';
import { selectCandidate } from '../api/endpoints';
import { describeError } from '../api/client';
import type { Candidate } from '../api/types';
import { dayLabel, num } from '../lib/format';
import { useFlow } from '../state/flow';
import { useSession } from '../state/session';
import { ChainView } from '../components/ChainView';
import { OptionRow } from '../components/OptionRow';
import { CheckoutBar } from '../components/CheckoutBar';
import { Mascot } from '../components/Mascot';
import { Banner, Button, Empty, PageHead, useToast } from '../components/ui';

/** "Milk type: Semi-skimmed" → ["semi-skimmed"]: the answer to the AI's question, if any. */
const clarifiedOf = (notes?: string | null) => (notes?.includes(': ') ? [notes.split(': ').pop()!.toLowerCase()] : []);

const newKey = () => (typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `idem-${Date.now()}-${Math.random().toString(16).slice(2)}`);

export function OptionsPage() {
  const { flow, patch } = useFlow();
  const { mode } = useSession();
  const navigate = useNavigate();
  const toast = useToast();
  const [showMore, setShowMore] = useState(false);
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
        actions={!working && <Button variant="ghost" onClick={() => navigate('/request')}><PencilLine size={15} /> Edit request</Button>} />

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
            <button type="button" className="opts-more" onClick={() => setShowMore((v) => !v)} aria-expanded={showMore}>
              {showMore ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
              {showMore ? 'Show fewer options' : `Show ${more.length} more option${more.length > 1 ? 's' : ''}`}
            </button>
          )}
        </>
      )}

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

      </>)}

      {flow.selection && <CheckoutBar selection={flow.selection} onClear={() => patch({ selection: null })} />}
    </>
  );
}
