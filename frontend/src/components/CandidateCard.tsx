import { AlertTriangle, Award, Check, ChevronDown, Lightbulb, Repeat, ShieldCheck, Truck } from 'lucide-react';
import type { Candidate, Level } from '../api/types';
import { ALLERGEN_STATUS, AVAILABILITY, BADGE, dayLabel, eur, levelOf, num, outcomeOf } from '../lib/format';
import { Button, Pill } from './ui';

const BAR_W: Record<string, string> = { HIGH: '100%', MEDIUM: '62%', LOW: '28%' };
const BAR_C: Record<string, string> = { HIGH: 'var(--ok)', MEDIUM: 'var(--warn)', LOW: 'var(--bad)' };

export function TrustMeter({ t }: { t: Candidate['cards']['trust'] }) {
  const dims: [string, Level][] = [['Evidence', t.evidence], ['Match', t.match], ['Fulfilment', t.fulfilment], ['Compliance', t.compliance]];
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

export function CandidateCard({ c, selected, featured, onSelect, loading, mode, all }: {
  c: Candidate; selected: boolean; featured?: boolean; onSelect: () => void; loading: boolean;
  mode: 'SEPARATE' | 'SELF'; all: Candidate[];
}) {
  const line = c.lines[0];
  const t = c.cards.trust;
  const out = outcomeOf(c.outcome_preview, mode);
  const av = AVAILABILITY[line.available_on_date];
  const nameOf = (id: string) => all.find((x) => x.id === id)?.lines[0].name ?? id;

  return (
    <article className={`cand ${selected ? 'sel' : ''} ${featured ? 'featured' : ''}`} aria-label={line.name}>
      <div className="row between">
        <span className="badge">{c.badge ? <><Award size={14} /> {BADGE[c.badge]}</> : <>#{c.rank}</>}</span>
        <Pill tone={out.tone}>{out.label}</Pill>
      </div>

      <div>
        <div className="name">{line.name}</div>
        <div className="small muted" style={{ marginTop: 4 }}>
          {line.brand} · {num(line.sales_units)} × {line.sales_unit} = {num(line.qty_covered.value)} {line.qty_covered.unit}
          {line.overbuy && <> · +{num(line.overbuy.value)} {line.overbuy.unit} over</>}
        </div>
      </div>

      <div>
        <div className="price">{eur(c.totals?.gross_cents)}<small>incl. VAT</small></div>
        <div className="row wrap small" style={{ gap: 8, marginTop: 6 }}>
          <span className="row" style={{ gap: 5 }}><Truck size={14} className="muted" /> by {dayLabel(c.delivery_date)}</span>
          <Pill tone={av.tone}>{av.label}</Pill>
        </div>
      </div>

      <p className="why">{c.explanation}</p>

      <div className="stack sm">
        <div className="row between small">
          <span className="row" style={{ gap: 6 }}><ShieldCheck size={15} className="muted" /> Trust</span>
          <Pill tone={levelOf(t.overall).tone}>{levelOf(t.overall).label}</Pill>
        </div>
        <TrustMeter t={t} />
        {t.how_to_improve && <div className="xs muted">How to improve: {t.how_to_improve}</div>}
      </div>

      <div>
        <details className="details">
          <summary><ShieldCheck size={14} /> Allergen check <ChevronDown size={14} className="chev" /></summary>
          <div className="details-body">
            {c.cards.allergen.rows.map((r, i) => {
              const s = ALLERGEN_STATUS[r.status];
              return (
                <div key={i} className="row wrap" style={{ gap: 8 }}>
                  <span>{r.requirement.label ?? r.requirement.code}</span>
                  <Pill tone={s.tone}>{s.label}</Pill>
                  <span className="xs muted">grade {r.trust_grade} · {r.source}</span>
                </div>
              );
            })}
            {(c.cards.allergen.notes ?? []).map((n) => (
              <div key={n} className="row top xs" style={{ gap: 6, color: 'var(--warn)' }}><AlertTriangle size={13} style={{ flex: 'none', marginTop: 2 }} /> {n}</div>
            ))}
            {c.cards.allergen.data_updated_at && <div className="xs muted">Data updated {new Date(c.cards.allergen.data_updated_at).toLocaleDateString('en-GB')}</div>}
          </div>
        </details>
        <details className="details">
          <summary><Lightbulb size={14} /> Tips ({c.cards.tips?.length ?? 0}) <ChevronDown size={14} className="chev" /></summary>
          <div className="details-body">
            {(c.cards.tips ?? []).map((tip, i) => <div key={i} className="small text-2">• {tip.text}</div>)}
            {!c.cards.tips?.length && <div className="small muted">None</div>}
          </div>
        </details>
        <details className="details">
          <summary><Repeat size={14} /> Alternatives ({c.cards.alternatives?.length ?? 0}) <ChevronDown size={14} className="chev" /></summary>
          <div className="details-body">
            {(c.cards.alternatives ?? []).map((a) => <div key={a.candidate_id} className="small"><b>{nameOf(a.candidate_id)}</b><div className="text-2">{a.tradeoff}</div></div>)}
            {!c.cards.alternatives?.length && <div className="small muted">None</div>}
          </div>
        </details>
      </div>

      <div className="xs muted">{(c.outcome_reasons ?? []).join(' · ')}</div>

      <Button variant={featured && !selected ? 'primary' : 'default'} block loading={loading} onClick={onSelect} aria-pressed={selected}>
        {selected ? <><Check size={16} /> Selected</> : 'Choose this'}
      </Button>
    </article>
  );
}
