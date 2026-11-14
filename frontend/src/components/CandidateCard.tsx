import { AlertTriangle, Check, ChevronDown } from 'lucide-react';
import type { Candidate, Level } from '../api/types';
import { ALLERGEN_STATUS, AVAILABILITY, BADGE, dayLabel, eur, levelOf, num, outcomeOf } from '../lib/format';
import { Button, Pill } from './ui';

const BAR_W: Record<string, string> = { HIGH: '100%', MEDIUM: '62%', LOW: '28%' };
const BAR_C: Record<string, string> = { HIGH: 'var(--ok)', MEDIUM: 'var(--warn)', LOW: 'var(--bad)' };

export function TrustMeter({ t }: { t: Candidate['cards']['trust'] }) {
  const dims: [string, Level][] = [['Evidence', t.evidence], ['Match', t.match], ['Delivery', t.fulfilment], ['Protocol', t.compliance]];
  return (
    <div className="meter" aria-label="Trust by dimension">
      {dims.map(([label, l]) => (
        <div key={label} title={`${label}: ${levelOf(l).label}`}>
          <div className="bar"><i style={{ width: BAR_W[l], background: BAR_C[l] }} /></div>
          <div className="lbl">{label}</div>
        </div>
      ))}
    </div>
  );
}

/** One option. Only what is needed to decide is visible; evidence lives under "Details". */
export function CandidateCard({ c, selected, featured, onSelect, loading, mode, all }: {
  c: Candidate; selected: boolean; featured?: boolean; onSelect: () => void; loading: boolean;
  mode: 'SEPARATE' | 'SELF'; all: Candidate[];
}) {
  const line = c.lines[0];
  const t = c.cards.trust;
  const out = outcomeOf(c.outcome_preview, mode);
  const av = AVAILABILITY[line.available_on_date];
  const trust = levelOf(t.overall);
  const nameOf = (id: string) => all.find((x) => x.id === id)?.lines[0].name ?? id;

  return (
    <article className={`cand ${selected ? 'sel' : ''} ${featured ? 'featured' : ''}`} aria-label={line.name}>
      <div className="row between wrap" style={{ gap: 8 }}>
        <span className={`badge ${c.badge ? '' : 'plain'}`}>{c.badge ? BADGE[c.badge] : `Option ${c.rank}`}</span>
        {c.outcome_preview !== 'AUTO' && <Pill tone={out.tone}>{out.label}</Pill>}
      </div>

      <div>
        <div className="name">{line.name}</div>
        <div className="small muted" style={{ marginTop: 2 }}>{num(line.sales_units)} × {line.sales_unit} · {num(line.qty_covered.value)} {line.qty_covered.unit}</div>
      </div>

      <div>
        <div className="price">{eur(c.totals?.gross_cents)}</div>
        <div className="small muted">
          incl. VAT · arrives {dayLabel(c.delivery_date)}
          {line.available_on_date !== 'YES' && <> · <span style={{ color: `var(--${av.tone === 'neutral' ? 'muted' : av.tone})` }}>{av.label.toLowerCase()}</span></>}
        </div>
      </div>

      <p className="why">{c.explanation}</p>

      <div className="small row" style={{ gap: 6 }}>
        <span className={`dot tone-dot-${trust.tone}`} aria-hidden />Trust {trust.label.toLowerCase()}
      </div>

      <details className="details">
        <summary>Details <ChevronDown size={14} className="chev" /></summary>
        <div className="details-body">
          <div className="stack sm">
            <TrustMeter t={t} />
            {t.how_to_improve && <div className="xs muted">{t.how_to_improve}</div>}
          </div>
          <div className="stack sm">
            {c.cards.allergen.rows.map((r, i) => (
              <div key={i} className="row" style={{ gap: 8 }}>
                <span className="grow">{r.requirement.label ?? r.requirement.code}</span>
                <Pill tone={ALLERGEN_STATUS[r.status].tone}>{ALLERGEN_STATUS[r.status].label}</Pill>
              </div>
            ))}
            {(c.cards.allergen.notes ?? []).map((n) => (
              <div key={n} className="row top xs" style={{ gap: 6, color: 'var(--warn)' }}><AlertTriangle size={13} style={{ flex: 'none', marginTop: 2 }} />{n}</div>
            ))}
            {c.cards.allergen.rows[0] && <div className="xs muted">Source: {c.cards.allergen.rows[0].source}</div>}
          </div>
          {(c.cards.tips?.length ?? 0) > 0 && (
            <ul className="plain">{c.cards.tips!.filter((tip) => tip.kind !== 'QUOTE_TIMER').map((tip, i) => <li key={i}>{tip.text}</li>)}</ul>
          )}
          {(c.cards.alternatives ?? []).map((a) => (
            <div key={a.candidate_id} className="small"><span className="muted">Instead: </span>{nameOf(a.candidate_id)}. {a.tradeoff}.</div>
          ))}
        </div>
      </details>

      <Button variant={featured && !selected ? 'primary' : 'default'} block loading={loading} onClick={onSelect} aria-pressed={selected}>
        {selected ? <><Check size={16} /> Selected</> : 'Choose'}
      </Button>
    </article>
  );
}
