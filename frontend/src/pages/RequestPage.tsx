import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, Check, MessageSquareText, PencilLine, RotateCcw, SlidersHorizontal, Sparkles } from 'lucide-react';
import { createRequest, getFacets, listProfiles, parseRequest, rejectNode } from '../api/endpoints';
import { describeError } from '../api/client';
import type { Facets, Profile, Requirement, StructuredRequest } from '../api/types';
import { useSession } from '../state/session';
import { useFlow } from '../state/flow';
import { dayLabel, num } from '../lib/format';
import { Banner, Button, ErrorState, PageHead, Pill, Segmented, Skeleton, useAsync, useToast } from '../components/ui';

type Sub = StructuredRequest['substitution'];
interface Panel {
  category: string;
  reqs: Requirement[];
  qty: string;
  unit: 'L' | 'kg' | 'pcs';
  date: string;
  budget: string;
  substitution: Sub;
}

const reqKey = (r: Requirement) => `${r.kind}:${r.code}`;
const isoIn = (days: number) => new Date(Date.now() + days * 86_400_000).toISOString().slice(0, 10);

function panelFrom(s: StructuredRequest): Panel {
  return {
    category: s.category,
    reqs: s.requirements ?? [],
    qty: String(s.quantity.value),
    unit: s.quantity.unit,
    date: s.delivery_date,
    budget: s.budget_cents ? String(s.budget_cents / 100) : '',
    substitution: s.substitution ?? 'SAME_CATEGORY',
  };
}

export function RequestPage() {
  const { workspace } = useSession();
  const { flow, patch } = useFlow();
  const navigate = useNavigate();
  const toast = useToast();

  const facets = useAsync(getFacets, []);
  const profiles = useAsync(() => listProfiles(workspace.buyerId!), [workspace.buyerId]);
  const [profileId, setProfileId] = useState<string>('');
  const profile: Profile | undefined = profiles.data?.find((p) => p.id === profileId) ?? profiles.data?.[0];

  const [parsing, setParsing] = useState(false);
  const [submitting, setSubmitting] = useState<'nl' | 'panel' | null>(null);
  const [answer, setAnswer] = useState<string | null>(null);
  const [panel, setPanel] = useState<Panel>(() =>
    flow.panel ? panelFrom(flow.panel) : { category: '', reqs: [], qty: '', unit: 'L', date: '', budget: '', substitution: 'SAME_CATEGORY' },
  );
  const [touched, setTouched] = useState(false);
  const [flash, setFlash] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);

  // Pre-fill panel defaults from the profile (only fields the user has not filled yet).
  useEffect(() => {
    if (!profile || flow.panel) return;
    setPanel((p) => ({
      ...p,
      substitution: profile.body.defaults?.substitution ?? p.substitution,
      date: p.date || (profile.body.defaults?.lead_days != null ? isoIn(profile.body.defaults.lead_days) : ''),
    }));
  }, [profile, flow.panel]);

  // Correcting step ①: start from what the system understood last time.
  useEffect(() => {
    if (flow.correcting && flow.request) setPanel(panelFrom(flow.request.structured));
  }, [flow.correcting, flow.request]);

  const panelErrors = useMemo(() => {
    const e: Partial<Record<'category' | 'qty' | 'date', string>> = {};
    if (!panel.category) e.category = 'Pick a category';
    if (!(Number(panel.qty) > 0)) e.qty = 'Enter a quantity above 0';
    if (!panel.date) e.date = 'Pick a delivery date';
    else if (panel.date < isoIn(0)) e.date = 'Date is in the past';
    return e;
  }, [panel]);

  const understand = async () => {
    setParsing(true);
    setAnswer(null);
    try {
      const parse = await parseRequest(flow.rawText, profile?.id);
      patch({ parse });
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setParsing(false);
    }
  };

  const submit = async (structured: StructuredRequest, inputMode: 'NATURAL_LANGUAGE' | 'PANEL') => {
    setSubmitting(inputMode === 'PANEL' ? 'panel' : 'nl');
    try {
      const request = flow.correcting && flow.request
        ? await rejectNode(flow.request.request_id, 1, { reason: flow.correcting, params: { structured } })
        : await createRequest({
            protocol_id: workspace.protocolId!,
            profile_id: profile?.id ?? null,
            input_mode: inputMode,
            raw_text: inputMode === 'NATURAL_LANGUAGE' ? flow.rawText : null,
            structured,
          });
      patch({
        request, panel: structured, regenFrom: flow.correcting ? 1 : null, correcting: null,
        selection: null, order: null, sentToApproval: false, approvalResult: null,
      });
      navigate('/options');
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setSubmitting(null);
    }
  };

  const confirmNl = () => {
    const p = flow.parse!;
    const amb = p.ambiguities?.[0];
    submit({ ...p.structured, notes: amb && answer ? `${amb.question} → ${answer}` : p.structured.notes }, 'NATURAL_LANGUAGE');
  };

  const confirmPanel = () => {
    setTouched(true);
    if (Object.keys(panelErrors).length) return;
    const cat = facets.data?.categories.find((c) => c.code === panel.category);
    submit({
      category: panel.category,
      category_label: cat?.label ?? null,
      requirements: panel.reqs,
      quantity: { value: Number(panel.qty), unit: panel.unit },
      delivery_date: panel.date,
      budget_cents: panel.budget ? Math.round(Number(panel.budget) * 100) : null,
      substitution: panel.substitution,
      notes: null,
    }, 'PANEL');
  };

  const editInPanel = () => {
    if (!flow.parse) return;
    setPanel(panelFrom(flow.parse.structured));
    setFlash(true);
    panelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setTimeout(() => setFlash(false), 1400);
    toast('Moved into the panel. Edit anything, then confirm.', 'info');
  };

  return (
    <>
      <PageHead
        eyebrow={flow.correcting ? 'Step ① · Correcting' : 'Step 1 · Describe'}
        title={flow.correcting ? 'Fix what was misunderstood' : 'What do you need?'}
        desc="Say it in your own words or pick from the panel. Nothing is searched or ordered until you confirm."
      />

      {flow.correcting && (
        <div style={{ marginBottom: 16 }}>
          <Banner tone="info" title={`You marked step ① “Understand request” as not right`}
            action={<Button size="sm" variant="ghost" onClick={() => { patch({ correcting: null }); navigate('/options'); }}>Cancel</Button>}>
            Edit the panel below and confirm. Steps ② to ④ will be regenerated; nothing else changes.
          </Banner>
        </div>
      )}

      <div className="grid two" style={{ alignItems: 'start' }}>
        {/* ── Path A: natural language ── */}
        {!flow.correcting && (
          <section className="card path-card" aria-labelledby="nl-title">
            <div className="row">
              <div className="icon-tile"><MessageSquareText size={18} /></div>
              <div className="grow">
                <h2 id="nl-title">In your own words</h2>
                <div className="small muted">AI reads it back to you before anything happens</div>
              </div>
            </div>

            <div className="field">
              <label htmlFor="profile">Buying profile</label>
              {profiles.loading ? <Skeleton h={40} /> : profiles.error ? <ErrorState error={profiles.error} onRetry={profiles.reload} /> : (
                <select id="profile" value={profile?.id ?? ''} onChange={(e) => setProfileId(e.target.value)}>
                  {profiles.data?.map((p) => <option key={p.id} value={p.id}>{p.body.name}</option>)}
                </select>
              )}
              {profile && (
                <div className="chips" style={{ gap: 6 }}>
                  {profile.body.hard_exclusions?.brands?.map((b) => <Pill key={b} tone="bad">Never {b}</Pill>)}
                  {profile.body.soft_preferences?.prefer_large_packs && <Pill tone="neutral">Prefers large packs</Pill>}
                  {profile.body.soft_preferences?.countries?.includes('fi') && <Pill tone="neutral">Prefers Finnish</Pill>}
                </div>
              )}
            </div>

            <div className="field">
              <label htmlFor="nl">Your request</label>
              <textarea id="nl" rows={3} value={flow.rawText} placeholder="e.g. 1000 litres of lactose-free milk by Monday"
                onChange={(e) => patch({ rawText: e.target.value, parse: null })} />
            </div>

            {!flow.parse && (
              <Button variant="primary" loading={parsing} disabled={!flow.rawText.trim()} onClick={understand}>
                <Sparkles size={16} /> Let AI understand
              </Button>
            )}

            {flow.parse && (
              <div className="understood" aria-live="polite">
                <div className="eyebrow">Here is what I understood</div>
                <Sentence s={flow.parse.structured} />
                {(flow.parse.profile_applied?.length ?? 0) > 0 && (
                  <div className="small muted">Profile applied: {flow.parse.profile_applied!.join(' · ')}</div>
                )}
                {flow.parse.ambiguities?.[0] && (
                  <div className="stack sm">
                    <div className="small strong">Please confirm: {flow.parse.ambiguities[0].question}</div>
                    <div className="chips" role="radiogroup" aria-label={flow.parse.ambiguities[0].question}>
                      {flow.parse.ambiguities[0].options?.map((o) => (
                        <button key={o} type="button" className="chip" role="radio" aria-pressed={answer === o} aria-checked={answer === o} onClick={() => setAnswer(o)}>
                          {answer === o && <Check size={14} />} {o}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                <div className="row wrap">
                  <Button variant="primary" loading={submitting === 'nl'}
                    disabled={!!flow.parse.ambiguities?.length && !answer}
                    title={flow.parse.ambiguities?.length && !answer ? 'Answer the question above first' : undefined}
                    onClick={confirmNl}>
                    That’s right, get options <ArrowRight size={16} />
                  </Button>
                  <Button onClick={editInPanel}><PencilLine size={15} /> Edit in the panel</Button>
                  <Button variant="ghost" onClick={() => patch({ parse: null })}><RotateCcw size={15} /> Rephrase</Button>
                </div>
              </div>
            )}
            <div className="xs muted mono">POST /v1/requests/parse → POST /v1/requests</div>
          </section>
        )}

        {/* ── Path B: panel ── */}
        <section ref={panelRef} className="card path-card" aria-labelledby="panel-title"
          style={flash ? { borderColor: 'var(--accent)', boxShadow: 'var(--accent-glow)' } : undefined}>
          <div className="row">
            <div className="icon-tile info"><SlidersHorizontal size={18} /></div>
            <div className="grow">
              <h2 id="panel-title">{flow.correcting ? 'Corrected request' : 'Or pick from the panel'}</h2>
              <div className="small muted">No AI involved: exactly what you tick is searched</div>
            </div>
          </div>
          {facets.loading ? <Skeleton h={320} /> : facets.error ? <ErrorState error={facets.error} onRetry={facets.reload} /> : (
            <PanelForm facets={facets.data!} panel={panel} setPanel={setPanel} errors={touched ? panelErrors : {}} />
          )}
          <Button variant={flow.parse && !flow.correcting ? 'default' : 'primary'} loading={submitting === 'panel'} onClick={confirmPanel}>
            {flow.correcting ? 'Confirm and regenerate from step ②' : 'Confirm and get options'} <ArrowRight size={16} />
          </Button>
          <div className="xs muted mono">GET /v1/catalog/facets · {flow.correcting ? 'POST /v1/requests/{id}/nodes/1/reject' : 'POST /v1/requests'}</div>
        </section>
      </div>
    </>
  );
}

function Sentence({ s }: { s: StructuredRequest }) {
  const T = ({ children }: { children: React.ReactNode }) => <span className="token">{children}</span>;
  const subs: Record<string, string> = { SAME_CATEGORY: 'same-category substitutes OK', NONE: 'no substitutes', ANY: 'any substitute OK' };
  return (
    <p className="sentence">
      <T>{s.category_label ?? s.category}</T>
      {(s.requirements ?? []).map((r) => <span key={reqKey(r)}>, <T>{(r.label ?? r.code).toLowerCase()}</T></span>)}
      , <T>{num(s.quantity.value)} {s.quantity.unit}</T>, delivered by <T>{dayLabel(s.delivery_date)}</T>; {subs[s.substitution ?? 'SAME_CATEGORY']}
      {s.budget_cents ? <>, budget <T>€{num(s.budget_cents / 100)}</T></> : null}.
    </p>
  );
}

function PanelForm({ facets, panel, setPanel, errors }: {
  facets: Facets; panel: Panel; setPanel: (fn: (p: Panel) => Panel) => void; errors: Partial<Record<'category' | 'qty' | 'date', string>>;
}) {
  const has = (r: Requirement) => panel.reqs.some((x) => reqKey(x) === reqKey(r));
  const toggle = (r: Requirement) =>
    setPanel((p) => ({ ...p, reqs: has(r) ? p.reqs.filter((x) => reqKey(x) !== reqKey(r)) : [...p.reqs, r] }));
  const Chip = ({ r, text }: { r: Requirement; text: string }) => (
    <button type="button" className="chip" aria-pressed={has(r)} onClick={() => toggle(r)}>
      {has(r) && <Check size={14} />}{text}
    </button>
  );
  return (
    <div className="stack">
      <div className="field">
        <label htmlFor="cat">Category</label>
        <select id="cat" value={panel.category} aria-invalid={!!errors.category} onChange={(e) => setPanel((p) => ({ ...p, category: e.target.value }))}>
          <option value="">Choose…</option>
          {facets.categories.map((c) => <option key={c.code} value={c.code}>{c.label}</option>)}
        </select>
        {errors.category && <span className="error-text">{errors.category}</span>}
      </div>

      <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="label" style={{ marginBottom: 6 }}>Must have</legend>
        <div className="chips">
          {facets.claims.map((c) => <Chip key={c.code} r={{ kind: 'CLAIM', code: c.code, label: c.label }} text={c.label} />)}
        </div>
      </fieldset>
      <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="label" style={{ marginBottom: 6 }}>Must be declared free from</legend>
        <div className="chips">
          {facets.allergens.map((a) => <Chip key={a.code} r={{ kind: 'ALLERGEN_FREE', code: a.code, label: `No ${a.label.toLowerCase()}` }} text={`No ${a.label.toLowerCase()}`} />)}
        </div>
      </fieldset>
      <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="label" style={{ marginBottom: 6 }}>Labels</legend>
        <div className="chips">
          {facets.labels.map((l) => <Chip key={l.code} r={{ kind: 'LABEL', code: l.code, label: l.label }} text={l.label} />)}
        </div>
      </fieldset>

      <div className="form-grid">
        <div className="field">
          <label htmlFor="qty">Quantity</label>
          <div className="input-group">
            <input id="qty" inputMode="decimal" value={panel.qty} placeholder="1000" aria-invalid={!!errors.qty}
              onChange={(e) => setPanel((p) => ({ ...p, qty: e.target.value.replace(/[^\d.]/g, '') }))} />
            <select aria-label="Unit" value={panel.unit} style={{ width: 80 }} onChange={(e) => setPanel((p) => ({ ...p, unit: e.target.value as Panel['unit'] }))}>
              {(facets.units ?? ['L', 'kg', 'pcs']).map((u) => <option key={u}>{u}</option>)}
            </select>
          </div>
          {errors.qty && <span className="error-text">{errors.qty}</span>}
        </div>
        <div className="field">
          <label htmlFor="date">Delivery date</label>
          <input id="date" type="date" value={panel.date} min={isoIn(0)} aria-invalid={!!errors.date} onChange={(e) => setPanel((p) => ({ ...p, date: e.target.value }))} />
          {errors.date && <span className="error-text">{errors.date}</span>}
        </div>
        <div className="field">
          <label htmlFor="budget">Budget cap <span className="muted">(optional)</span></label>
          <input id="budget" inputMode="decimal" value={panel.budget} placeholder="€" onChange={(e) => setPanel((p) => ({ ...p, budget: e.target.value.replace(/[^\d.]/g, '') }))} />
        </div>
      </div>
      <div className="field">
        <span className="label">Substitutes</span>
        <Segmented<Sub> label="Substitutes" value={panel.substitution}
          options={[['SAME_CATEGORY', 'Same category OK'], ['NONE', 'No substitutes'], ['ANY', 'Any']]}
          onChange={(v) => setPanel((p) => ({ ...p, substitution: v }))} />
        <span className="help">The protocol still decides which substitutes need approval.</span>
      </div>
    </div>
  );
}
