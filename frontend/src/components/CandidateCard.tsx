import type { ReactNode } from 'react';
import {
  AlertTriangle, BadgeCheck, Box, Check, ChevronDown, CircleAlert, Coins, Info, Leaf, Ruler, Thermometer, Truck,
} from 'lucide-react';
import type { Candidate, Level } from '../api/types';
import { ALLERGEN_STATUS, AVAILABILITY, BADGE, dayLabel, eur, levelOf, num, outcomeOf } from '../lib/format';
import { Button, Pill } from './ui';

const BAR_W: Record<string, string> = { HIGH: '100%', MEDIUM: '62%', LOW: '28%' };
const BAR_C: Record<string, string> = { HIGH: 'var(--ok)', MEDIUM: 'var(--warn)', LOW: 'var(--bad)' };

/** Compact four-bar meter (kept for other surfaces). */
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

// ───────────── Details, ordered by what matters to the decision ─────────────

const STEPS: Record<string, number> = { HIGH: 3, MEDIUM: 2, LOW: 1 };
const DIMENSIONS: { key: 'evidence' | 'match' | 'fulfilment' | 'compliance'; label: string; asks: string }[] = [
  { key: 'evidence', label: 'Evidence', asks: 'Is the product data from a reliable source?' },
  { key: 'match', label: 'Match', asks: 'Does it fit what you asked for?' },
  { key: 'fulfilment', label: 'Delivery', asks: 'Will it arrive on time and in full?' },
  { key: 'compliance', label: 'Protocol', asks: 'Is it within your purchasing rules?' },
];
const TIP_ICON: Record<string, ReactNode> = {
  STORAGE: <Thermometer size={16} />,
  COLD_STORAGE_SPACE: <Box size={16} />,
  RELIABILITY: <Truck size={16} />,
  DEPOSIT: <Coins size={16} />,
  ROUNDING: <Ruler size={16} />,
  CARBON: <Leaf size={16} />,
  LABEL: <BadgeCheck size={16} />,
};

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="dsec">
      <h4 className="dsec-title">{title}</h4>
      {children}
    </section>
  );
}

function TrustBreakdown({ t, nameFor }: { t: Candidate['cards']['trust']; nameFor: (s: string) => string }) {
  // The improvement note explains the weakest dimension, so it sits right under that row.
  const weakest = DIMENSIONS.reduce((w, d) => (STEPS[t[d.key]] < STEPS[t[w.key]] ? d : w), DIMENSIONS[0]);
  return (
    <div className="trust-rows">
      {DIMENSIONS.map((d) => {
        const lv = levelOf(t[d.key]);
        return (
          <div key={d.key}>
            <div className="trust-row" title={d.asks}>
              <span>{d.label}</span>
              <span className="segs" aria-hidden>
                {[1, 2, 3].map((i) => <i key={i} className={i <= STEPS[t[d.key]] ? `on tone-dot-${lv.tone}` : ''} />)}
              </span>
              <span className={`lvl lvl-${lv.tone}`}>{lv.label}</span>
            </div>
            {t.how_to_improve && d.key === weakest.key && t[d.key] !== 'HIGH' && (
              <p className="trust-note"><Info size={14} aria-hidden />{nameFor(t.how_to_improve)}</p>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** One option. Only what is needed to decide is visible; the evidence lives under "Details". */
export function CandidateCard({ c, selected, featured, onSelect, onChooseAlt, loading, mode, all }: {
  c: Candidate; selected: boolean; featured?: boolean; onSelect: () => void; onChooseAlt?: (id: string) => void;
  loading: boolean; mode: 'SEPARATE' | 'SELF'; all: Candidate[];
}) {
  const line = c.lines[0];
  const t = c.cards.trust;
  const out = outcomeOf(c.outcome_preview, mode);
  const av = AVAILABILITY[line.available_on_date];
  const trust = levelOf(t.overall);
  const byId = (id: string) => all.find((x) => x.id === id);
  // Backend text may mention candidates by id ("cand_3"); people read product names.
  const nameFor = (s: string) => s.replace(/cand_\w+/g, (id) => byId(id)?.lines[0].name ?? 'another option');
  const rows = c.cards.allergen.rows;
  // The timer lives on the quote bar, and delivery reliability is already explained under Trust → Delivery.
  const tips = (c.cards.tips ?? []).filter((tip) => tip.kind !== 'QUOTE_TIMER' && !(tip.kind === 'RELIABILITY' && t.how_to_improve));
  const alts = c.cards.alternatives ?? [];
  const preview = ['requirements', 'trust', tips.length ? 'storage' : null, alts.length ? 'alternative' : null].filter(Boolean).join(', ');

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
        <summary>
          <span className="stack" style={{ gap: 0 }}>
            <span>Details</span>
            <span className="xs muted" style={{ fontWeight: 450 }}>{preview[0].toUpperCase() + preview.slice(1)}</span>
          </span>
          <ChevronDown size={16} className="chev" />
        </summary>
        <div className="details-body">
          {rows.length > 0 && (
            <Section title="Requirements">
              {rows.map((r, i) => {
                const st = ALLERGEN_STATUS[r.status];
                const ok = st.tone === 'ok';
                return (
                  <div key={i} className="req-row">
                    {ok ? <Check size={16} className="ok-ink" /> : <CircleAlert size={16} className="warn-text" />}
                    <span className="grow strong">{r.requirement.label ?? r.requirement.code}</span>
                    <span className={`lvl lvl-${st.tone}`}>{st.label}</span>
                  </div>
                );
              })}
              {(c.cards.allergen.notes ?? []).map((n) => (
                <p key={n} className="callout warn"><AlertTriangle size={15} aria-hidden />{n}</p>
              ))}
              <p className="xs muted">Source: {rows[0].source.replace(/^Manufacturer structured claim/, 'Manufacturer data')}</p>
            </Section>
          )}

          <Section title="Trust">
            <TrustBreakdown t={t} nameFor={nameFor} />
          </Section>

          {tips.length > 0 && (
            <Section title="Good to know">
              <ul className="tips">
                {tips.map((tip, i) => (
                  <li key={i}><span className="tip-icon" aria-hidden>{TIP_ICON[tip.kind] ?? <Info size={16} />}</span>{tip.text}</li>
                ))}
              </ul>
            </Section>
          )}

          {alts.length > 0 && (
            <Section title={alts.length > 1 ? 'Alternatives' : 'Alternative'}>
              {alts.map((a) => {
                const alt = byId(a.candidate_id);
                const [pro, con] = a.tradeoff.split(/,\s*but\s*/i);
                return (
                  <div key={a.candidate_id} className="alt">
                    <div className="row between top" style={{ gap: 12 }}>
                      <span className="strong">{alt?.lines[0].name ?? 'Another option'}</span>
                      {alt?.totals && <span className="strong nowrap num">{eur(alt.totals.gross_cents)}</span>}
                    </div>
                    <div className="alt-trade">
                      <span className="pro"><Check size={14} />{pro}</span>
                      {con && <span className="con"><span aria-hidden>–</span>{con}</span>}
                    </div>
                    {onChooseAlt && alt && (
                      <button type="button" className="btn ghost sm alt-cta" onClick={() => onChooseAlt(alt.id)}>Choose instead</button>
                    )}
                  </div>
                );
              })}
            </Section>
          )}
        </div>
      </details>

      <Button variant={featured && !selected ? 'primary' : 'default'} block loading={loading} onClick={onSelect} aria-pressed={selected}>
        {selected ? <><Check size={16} /> Selected</> : 'Choose'}
      </Button>
    </article>
  );
}
