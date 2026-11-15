// A protocol's terms as a form (categories, hard requirements, limits, end date). Used in step 0 to fix Aimo's draft
// and on the protocol page to change a signed protocol; the backend decides what is stricter and what is looser.
import { useState } from 'react';
import { Check } from 'lucide-react';
import type { Facets, ProtocolBody, Requirement } from '../api/types';
import { Button } from './ui';

/** A protocol's terms as a form: fixing a draft in step 0, or changing a signed protocol later. */
export function ProtocolEditor({ body, facets, onSave, onCancel, saveLabel = 'Save changes' }: {
  body: ProtocolBody; facets: Facets; onSave: (b: ProtocolBody) => Promise<void>; onCancel?: () => void; saveLabel?: string;
}) {
  const [cats, setCats] = useState<string[]>(body.allowed_categories);
  const [reqs, setReqs] = useState<Requirement[]>(body.hard_requirements ?? []);
  const [auto, setAuto] = useState(String(body.auto_approve_limit_cents / 100));
  const [max, setMax] = useState(String(body.max_order_cents / 100));
  const [until, setUntil] = useState(body.valid_until);
  const [saving, setSaving] = useState(false);

  const key = (r: Requirement) => `${r.kind}:${r.code}`;
  const has = (r: Requirement) => reqs.some((x) => key(x) === key(r));
  const toggleReq = (r: Requirement) => setReqs((rs) => (has(r) ? rs.filter((x) => key(x) !== key(r)) : [...rs, r]));
  const toggleCat = (code: string) => setCats((cs) => (cs.includes(code) ? cs.filter((c) => c !== code) : [...cs, code]));
  const options: Requirement[] = [
    ...facets.claims.map((c) => ({ kind: 'CLAIM' as const, code: c.code, label: c.label })),
    ...facets.allergens.map((a) => ({ kind: 'ALLERGEN_FREE' as const, code: a.code, label: `No ${a.label.toLowerCase()}` })),
  ];

  const autoN = Number(auto), maxN = Number(max);
  const problem = !cats.length ? 'Choose at least one product category'
    : !(autoN >= 0) || !(maxN > 0) ? 'Enter both amounts'
    : autoN > maxN ? 'The automatic limit cannot be above the maximum'
    : !until ? 'Choose an end date' : null;

  const save = async () => {
    if (problem) return;
    setSaving(true);
    await onSave({ ...body, allowed_categories: cats, hard_requirements: reqs,
                   auto_approve_limit_cents: Math.round(autoN * 100), max_order_cents: Math.round(maxN * 100), valid_until: until });
    setSaving(false);
  };

  return (
    <div className="stack">
      <div className="field">
        <span className="label">May buy</span>
        <div className="chips">
          {facets.categories.map((c) => (
            <button key={c.code} type="button" className="chip" aria-pressed={cats.includes(c.code)} onClick={() => toggleCat(c.code)}>
              {cats.includes(c.code) && <Check size={14} />}{c.label}
            </button>
          ))}
        </div>
      </div>
      <div className="field">
        <span className="label">Always <span className="muted small">optional</span></span>
        <div className="chips">
          {options.map((r) => (
            <button key={key(r)} type="button" className="chip" aria-pressed={has(r)} onClick={() => toggleReq(r)}>
              {has(r) && <Check size={14} />}{r.label}
            </button>
          ))}
        </div>
      </div>
      <div className="form-grid three-up">
        <div className="field">
          <label htmlFor="ob-auto">Orders on its own up to (€)</label>
          <input id="ob-auto" inputMode="decimal" value={auto} onChange={(e) => setAuto(e.target.value.replace(/[^\d.]/g, ''))} />
        </div>
        <div className="field">
          <label htmlFor="ob-max">Never above (€)</label>
          <input id="ob-max" inputMode="decimal" value={max} onChange={(e) => setMax(e.target.value.replace(/[^\d.]/g, ''))} />
        </div>
        <div className="field">
          <label htmlFor="ob-until">Valid until</label>
          <input id="ob-until" type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
        </div>
      </div>
      {problem && <span className="small" style={{ color: 'var(--bad)' }}>{problem}</span>}
      <div className="row">
        <Button variant="primary" onClick={save} loading={saving} disabled={!!problem}>{saveLabel}</Button>
        {onCancel && <Button variant="ghost" onClick={onCancel}>Cancel</Button>}
      </div>
    </div>
  );
}
