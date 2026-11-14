import { useId, useState, type ReactNode } from 'react';
import {
  AlertTriangle, BadgeCheck, Box, Check, ChevronDown, CircleAlert, Coins, Info, Leaf, Ruler, ShieldAlert, ShieldCheck, Thermometer, Truck,
} from 'lucide-react';
import type { Candidate } from '../api/types';
import { ALLERGEN_STATUS, AVAILABILITY, BADGE, dayLabel, eur, levelOf, num, outcomeOf } from '../lib/format';
import { Button } from './ui';

// Information architecture
//  · Collapsed row = the signals people compare in a few seconds: can it be ordered without approval (and why),
//    is the hard requirement verified, price and saving. Delivery date and overall trust are secondary meta.
//  · Details = the evidence behind those signals: where each requirement claim comes from, and one line per
//    trust dimension explaining its level, weakest first. Every sentence is derived from fields the API returns.

type Row = Candidate['cards']['allergen']['rows'][number];
type Dim = 'evidence' | 'match' | 'fulfilment' | 'compliance';

const STEPS: Record<string, number> = { HIGH: 3, MEDIUM: 2, LOW: 1 };
const DIMENSIONS: { key: Dim; label: string; asks: string }[] = [
  { key: 'evidence', label: 'Product data', asks: 'Is the product data from a reliable source?' },
  { key: 'match', label: 'Fit', asks: 'Does it fit what you asked for?' },
  { key: 'fulfilment', label: 'Delivery', asks: 'Will it arrive on time and in full?' },
  { key: 'compliance', label: 'Purchasing rules', asks: 'Is it within your purchasing protocol?' },
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
const RELIABILITY: Record<string, string> = {
  HIGH: 'strong delivery record',
  MEDIUM: 'mixed delivery record',
  LOW: 'weak delivery record, so delays are possible',
  UNKNOWN: 'no delivery record yet',
};
const SUBSTITUTE: Record<string, string> = {
  OFFICIAL: 'an official substitute',
  SAME_CATEGORY: 'a substitute from the same category',
  CROSS_CATEGORY: 'a substitute from another category',
};

/** A requirement counts as verified only when it's declared AND the source is trustworthy (grade A/B).
 *  A claim that comes only from the product name (grade C/D) is unverified. */
function checkOf(r: Row): 'verified' | 'unverified' | 'missing' {
  if (ALLERGEN_STATUS[r.status]?.tone !== 'ok') return 'missing';
  return r.trust_grade === 'A' || r.trust_grade === 'B' ? 'verified' : 'unverified';
}
const CHECK_WORD = { verified: 'Verified', unverified: 'Unverified', missing: 'Not declared' } as const;

/** "…the auto-approve limit of €1,000.00" → 100000 */
function limitFrom(reasons: string[]): number | null {
  for (const r of reasons) {
    const m = r.match(/limit of €\s?([\d,]+(?:\.\d+)?)/i);
    if (m) return Math.round(parseFloat(m[1].replace(/,/g, '')) * 100);
  }
  return null;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="dsec">
      <h4 className="dsec-title">{title}</h4>
      {children}
    </section>
  );
}

const REQ_CAUSE = 'REQ';

/** One option in the ranked list. */
export function OptionRow({ c, position, best, selected, onSelect, onChooseAlt, loading, mode, all }: {
  c: Candidate; position: number; best: Candidate; selected: boolean; onSelect: () => void; onChooseAlt?: (id: string) => void;
  loading: boolean; mode: 'SEPARATE' | 'SELF'; all: Candidate[];
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const line = c.lines[0];
  const t = c.cards.trust;
  const out = outcomeOf(c.outcome_preview, mode);
  const av = AVAILABILITY[line.available_on_date];
  const trust = levelOf(t.overall);
  const featured = c.id === best.id;
  const byId = (id: string) => all.find((x) => x.id === id);
  // Backend text may mention candidates by id ("cand_3"); people read product names.
  const nameFor = (s: string) => s.replace(/cand_\w+/g, (id) => byId(id)?.lines[0].name ?? 'another option');
  const reasons = (c.outcome_reasons ?? []).map(nameFor);
  const rows = c.cards.allergen.rows;
  const reqLabel = (r: Row) => r.requirement.label ?? r.requirement.code;
  const missing = rows.filter((r) => checkOf(r) === 'missing');
  const unverified = rows.filter((r) => checkOf(r) === 'unverified');
  const gross = c.totals?.gross_cents;
  const diff = !featured && gross != null && best.totals ? gross - best.totals.gross_cents : 0;
  const reliability = c.reliability_level ?? 'UNKNOWN';
  const delayRisk = reliability === 'LOW';

  // ── Signal 1: can it be ordered straight away, and the one reason why (not) ──
  const limit = limitFrom(reasons);
  const outcomeWhy = (() => {
    if (c.outcome_preview === 'AUTO') return limit != null && gross != null ? `${eur(limit - gross)} under your limit` : null;
    if (limit != null && gross != null && gross > limit) return `${eur(gross - limit)} over your limit`;
    if (unverified.length || missing.length) return REQ_CAUSE;
    return reasons[0] ? reasons[0].replace(/\.$/, '').toLowerCase() : null;
  })();

  // ── Signal 2: the hard requirements (folded into signal 1 when they are the reason for approval) ──
  const req = missing.length
    ? { tone: 'bad', text: missing.length === 1 ? `${reqLabel(missing[0])} not declared` : `${missing.length} requirements not declared` }
    : unverified.length
      ? { tone: 'warn', text: unverified.length === 1 ? `${reqLabel(unverified[0])} unverified` : `${unverified.length} requirements unverified` }
      : { tone: 'ok', text: rows.length === 1 ? `${reqLabel(rows[0])} verified` : 'All requirements verified' };

  // ── Details: one sentence per trust dimension, from real fields ──
  const reasonFor = (k: Dim): string => {
    switch (k) {
      case 'evidence': {
        if (!rows.length) return t.evidence === 'HIGH' ? 'Complete product data from Valio' : 'Product data is incomplete';
        if (missing.length || unverified.length) return `Based on ${rows.map((r) => r.source.toLowerCase()).filter((v, i, a) => a.indexOf(v) === i).join(', ')} (grade ${rows.map((r) => r.trust_grade).sort().pop()}), not manufacturer data`;
        return rows.some((r) => r.trust_grade === 'A') ? 'Backed by the manufacturer’s structured product data' : 'Backed by Valio product data';
      }
      case 'match': {
        const covers = `covers ${num(line.qty_covered.value)} ${line.qty_covered.unit}${line.overbuy ? ` (${num(line.overbuy.value)} ${line.overbuy.unit} extra)` : ''}`;
        const tier = line.substitution_tier && line.substitution_tier !== 'NONE' ? SUBSTITUTE[line.substitution_tier] : null;
        return tier ? `This is ${tier}; ${covers}` : `The product type you asked for; ${covers}`;
      }
      case 'fulfilment': {
        const when = `${av.label} on ${dayLabel(c.delivery_date)}`;
        return `${when}${line.available_on_date === 'YES' && delayRisk ? ', but' : ','} ${RELIABILITY[reliability]}`;
      }
      case 'compliance':
        return reasons.length ? reasons.join('. ') : out.label;
    }
  };
  // Weakest first: the reasons to hesitate come before the reassurance.
  const dims = [...DIMENSIONS].sort((a, b) => (STEPS[t[a.key]] ?? 0) - (STEPS[t[b.key]] ?? 0));
  // The timer lives on the quote bar, and delivery reliability is explained under Delivery.
  const tips = (c.cards.tips ?? []).filter((tip) => tip.kind !== 'QUOTE_TIMER' && tip.kind !== 'RELIABILITY');
  const alts = c.cards.alternatives ?? [];

  return (
    <li className={`opt ${featured ? 'featured' : ''} ${selected ? 'sel' : ''} ${open ? 'open' : ''}`} aria-label={`Option ${position}: ${line.name}`}>
      <span className="opt-rank" aria-hidden>{position}</span>

      <div className="opt-main">
        <div className="opt-title">
          <h3 className="opt-name">{line.name}</h3>
          {c.badge && <span className="opt-badge">{BADGE[c.badge]}</span>}
        </div>
        <div className="opt-pack">{num(line.sales_units)} × {line.sales_unit} · {num(line.qty_covered.value)} {line.qty_covered.unit}</div>

        <div className="opt-signals">
          <span className={`sig sig-${out.tone}`}>
            {out.tone === 'ok' ? <ShieldCheck size={16} aria-hidden /> : <ShieldAlert size={16} aria-hidden />}
            <b>{out.label}</b>{outcomeWhy && <span className="sig-why">· {outcomeWhy === REQ_CAUSE ? `${req.text[0].toLowerCase()}${req.text.slice(1)}` : outcomeWhy}</span>}
          </span>
          {rows.length > 0 && outcomeWhy !== REQ_CAUSE && (
            <span className={`sig sig-${req.tone}`}>
              {req.tone === 'ok' ? <Check size={16} aria-hidden /> : <CircleAlert size={16} aria-hidden />}
              <b>{req.text}</b>
            </span>
          )}
        </div>

        <div className="opt-meta">
          <span className={line.available_on_date !== 'YES' ? `m-${av.tone}` : delayRisk ? 'm-warn' : ''}>
            <Truck size={15} aria-hidden />Arrives {dayLabel(c.delivery_date)}
            {line.available_on_date !== 'YES' ? ` · ${av.label.toLowerCase()}` : delayRisk ? ' · delays possible' : ''}
          </span>
          <span><span className={`dot tone-dot-${trust.tone}`} aria-hidden />Trust {trust.label.toLowerCase()}</span>
          <button type="button" className="opt-more" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen((o) => !o)}>
            {open ? 'Hide evidence' : 'Show evidence'}<ChevronDown size={15} className="chev" aria-hidden />
          </button>
        </div>
      </div>

      <div className="opt-price">
        <div className="opt-amount num">{eur(gross)}</div>
        <div className="opt-vat">incl. VAT</div>
        {diff < 0 && <div className="opt-save">Save {eur(-diff)}</div>}
        {diff > 0 && <div className="opt-diff">+{eur(diff)} vs #1</div>}
      </div>

      <div className="opt-act">
        <Button variant={featured && !selected ? 'primary' : 'default'} loading={loading} onClick={onSelect} aria-pressed={selected}>
          {selected ? <><Check size={16} /> Selected</> : 'Choose'}
        </Button>
      </div>

      {open && (
        <div id={panelId} className="opt-panel">
          {rows.length > 0 && (
            <Section title="Your requirements">
              {rows.map((r, i) => {
                const check = checkOf(r);
                const tone = check === 'verified' ? 'ok' : check === 'missing' ? 'bad' : 'warn';
                const st = ALLERGEN_STATUS[r.status];
                return (
                  <div key={i} className="ev-row">
                    {tone === 'ok' ? <Check size={16} className="ok-ink" /> : <CircleAlert size={16} className={`${tone}-text`} />}
                    <div className="grow">
                      <div className="ev-head"><span className="strong">{reqLabel(r)}</span><span className={`lvl lvl-${tone}`}>{CHECK_WORD[check]}</span></div>
                      <p className="ev-why">
                        {check === 'verified' && `Declared in ${/^manufacturer structured/i.test(r.source) ? 'the manufacturer’s structured product data' : r.source.toLowerCase()} (grade ${r.trust_grade}).`}
                        {check === 'unverified' && `Found only in the ${r.source.toLowerCase().replace(/ only$/, '')} (grade ${r.trust_grade}). The manufacturer’s data doesn’t confirm it.`}
                        {check === 'missing' && `${st.label} according to ${r.source.toLowerCase()}.`}
                      </p>
                    </div>
                  </div>
                );
              })}
              {(c.cards.allergen.notes ?? []).map((n) => (
                <p key={n} className="callout warn"><AlertTriangle size={15} aria-hidden />{n}</p>
              ))}
            </Section>
          )}

          <Section title={`Trust: ${trust.label.toLowerCase()}`}>
            <div className="trust-rows">
              {dims.map((d) => {
                const lv = levelOf(t[d.key]);
                return (
                  <div key={d.key} className="ev-trust" title={d.asks}>
                    <div className="trust-row">
                      <span>{d.label}</span>
                      <span className="segs" aria-hidden>
                        {[1, 2, 3].map((i) => <i key={i} className={i <= (STEPS[t[d.key]] ?? 0) ? `on tone-dot-${lv.tone}` : ''} />)}
                      </span>
                      <span className={`lvl lvl-${lv.tone}`}>{lv.label}</span>
                    </div>
                    <p className="ev-why">{reasonFor(d.key)}</p>
                  </div>
                );
              })}
              {t.how_to_improve && <p className="trust-note"><Info size={14} aria-hidden />{nameFor(t.how_to_improve)}</p>}
            </div>
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
      )}
    </li>
  );
}
