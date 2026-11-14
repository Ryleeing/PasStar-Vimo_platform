// Step 1. One flow, not two competing paths:
//   type what you need → the AI fills in the form → check / adjust → find options.
// The form can also be filled by hand without typing anything.
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, ArrowUp, Check, ChevronDown, Sparkles } from 'lucide-react';
import { createRequest, getFacets, listProfiles, parseRequest, rejectNode } from '../api/endpoints';
import { describeError } from '../api/client';
import type { Facets, ParseResult, Requirement, StructuredRequest } from '../api/types';
import { useSession } from '../state/session';
import { useFlow } from '../state/flow';
import { Banner, Button, ErrorState, PageHead, Segmented, Skeleton, useAsync, useToast } from '../components/ui';

type Sub = StructuredRequest['substitution'];
interface Form {
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
const EMPTY: Form = { category: '', reqs: [], qty: '', unit: 'L', date: '', budget: '', substitution: 'SAME_CATEGORY' };

const formFrom = (s: StructuredRequest): Form => ({
  category: s.category,
  reqs: s.requirements ?? [],
  qty: String(s.quantity.value),
  unit: s.quantity.unit,
  date: s.delivery_date,
  budget: s.budget_cents ? String(s.budget_cents / 100) : '',
  substitution: s.substitution ?? 'SAME_CATEGORY',
});

export function RequestPage() {
  const { workspace } = useSession();
  const { flow, patch } = useFlow();
  const navigate = useNavigate();
  const toast = useToast();

  const facets = useAsync(getFacets, []);
  const profiles = useAsync(() => listProfiles(workspace.buyerId!), [workspace.buyerId]);
  const [profileId, setProfileId] = useState('');
  const profile = profiles.data?.find((p) => p.id === profileId) ?? profiles.data?.[0];

  const [form, setForm] = useState<Form>(() => (flow.panel ? formFrom(flow.panel) : EMPTY));
  const [answer, setAnswer] = useState<string | null>(null);
  const [parsing, setParsing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [tried, setTried] = useState(false);
  const [aiFilled, setAiFilled] = useState(false);

  // Profile defaults go into empty fields only.
  useEffect(() => {
    const d = profile?.body.defaults;
    if (!d || flow.panel) return;
    setForm((f) => ({ ...f, substitution: d.substitution ?? f.substitution, date: f.date || (d.lead_days != null ? isoIn(d.lead_days) : '') }));
  }, [profile, flow.panel]);

  // "Not right" on step ①: start from what the agent understood.
  useEffect(() => {
    if (flow.correcting && flow.request) setForm(formFrom(flow.request.structured));
  }, [flow.correcting, flow.request]);

  const question = flow.parse?.ambiguities?.[0];
  const errors = useMemo(() => {
    const e: Partial<Record<'category' | 'qty' | 'date', string>> = {};
    if (!form.category) e.category = 'Choose a category';
    if (!(Number(form.qty) > 0)) e.qty = 'Enter a quantity';
    if (!form.date) e.date = 'Choose a date';
    else if (form.date < isoIn(0)) e.date = 'Date is in the past';
    return e;
  }, [form]);
  const blocker = question && !answer ? `Answer “${question.question}” above` : Object.values(errors)[0];

  const understand = async () => {
    if (!flow.rawText.trim()) return;
    setParsing(true);
    setAnswer(null);
    try {
      const parse = await parseRequest(flow.rawText, profile?.id);
      patch({ parse });
      setForm(formFrom(parse.structured));
      setAiFilled(true);
      setTimeout(() => setAiFilled(false), 1600);
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setParsing(false);
    }
  };

  const submit = async () => {
    setTried(true);
    if (blocker) return;
    const cat = facets.data?.categories.find((c) => c.code === form.category);
    const structured: StructuredRequest = {
      category: form.category,
      category_label: cat?.label ?? null,
      requirements: form.reqs,
      quantity: { value: Number(form.qty), unit: form.unit },
      delivery_date: form.date,
      budget_cents: form.budget ? Math.round(Number(form.budget) * 100) : null,
      substitution: form.substitution,
      notes: question && answer ? `${question.question} → ${answer}` : null,
    };
    const fromText = !!flow.parse;
    setSubmitting(true);
    try {
      const request = flow.correcting && flow.request
        ? await rejectNode(flow.request.request_id, 1, { reason: flow.correcting, params: { structured } })
        : await createRequest({
            protocol_id: workspace.protocolId!,
            profile_id: profile?.id ?? null,
            input_mode: fromText ? 'NATURAL_LANGUAGE' : 'PANEL',
            raw_text: fromText ? flow.rawText : null,
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
      setSubmitting(false);
    }
  };

  return (
    <div className="narrow">
      <PageHead title={flow.correcting ? 'Correct your request' : 'What do you need?'} />

      {flow.correcting ? (
        <div style={{ marginBottom: 16 }}>
          <Banner tone="info" title="Fix the details below"
            action={<Button size="sm" variant="ghost" onClick={() => { patch({ correcting: null }); navigate('/options'); }}>Cancel</Button>}>
            The options are found again from here on.
          </Banner>
        </div>
      ) : (
        <>
          {/* ── Message box ── */}
          <div className="composer">
            <textarea
              aria-label="Describe what you need"
              rows={2}
              value={flow.rawText}
              placeholder="e.g. 1000 litres of lactose-free milk by Monday"
              onChange={(e) => patch({ rawText: e.target.value })}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  understand();
                }
              }}
            />
            <div className="composer-foot">
              {profiles.data && profiles.data.length > 0 && (
                <label className="profile-pick">
                  <span className="muted">Profile</span>
                  <select value={profile?.id ?? ''} onChange={(e) => setProfileId(e.target.value)}>
                    {profiles.data.map((p) => <option key={p.id} value={p.id}>{p.body.name}</option>)}
                  </select>
                </label>
              )}
              <Button variant="primary" size="sm" className="ml-auto" loading={parsing} disabled={!flow.rawText.trim()} onClick={understand} aria-label="Fill in the form from my message">
                {!parsing && <ArrowUp size={16} />} Fill in
              </Button>
            </div>
          </div>

          {flow.parse && <AiReply parse={flow.parse} answer={answer} setAnswer={setAnswer} highlight={tried && !!question && !answer} />}
        </>
      )}

      {/* ── The form ── */}
      <section className={`card form-card ${aiFilled ? 'ai-filled' : ''}`} aria-label="Request details">
        {facets.loading ? <Skeleton h={300} /> : facets.error ? <ErrorState error={facets.error} onRetry={facets.reload} /> : (
          <DetailsForm facets={facets.data!} form={form} setForm={setForm} errors={tried ? errors : {}} />
        )}
      </section>

      <div className="form-actions">
        {tried && blocker && <span className="small" style={{ color: 'var(--bad)' }}>{blocker}</span>}
        <Button variant="primary" size="lg" className="ml-auto" loading={submitting} onClick={submit}>
          Find options <ArrowRight size={17} />
        </Button>
      </div>
    </div>
  );
}

function AiReply({ parse, answer, setAnswer, highlight }: { parse: ParseResult; answer: string | null; setAnswer: (v: string) => void; highlight: boolean }) {
  const q = parse.ambiguities?.[0];
  return (
    <div className="ai-reply" aria-live="polite">
      <span className="ai-avatar" aria-hidden><Sparkles size={14} /></span>
      <div className="stack sm grow">
        <p>I’ve filled in the details below. Check them, then find options.</p>
        {q && (
          <div className={`stack sm ${highlight ? 'needs-answer' : ''}`}>
            <p className="strong">{q.question}</p>
            <div className="chips" role="radiogroup" aria-label={q.question}>
              {q.options?.map((o) => (
                <button key={o} type="button" className="chip" role="radio" aria-checked={answer === o} aria-pressed={answer === o} onClick={() => setAnswer(o)}>
                  {answer === o && <Check size={14} />}{o}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function DetailsForm({ facets, form, setForm, errors }: {
  facets: Facets; form: Form; setForm: (fn: (f: Form) => Form) => void; errors: Partial<Record<'category' | 'qty' | 'date', string>>;
}) {
  const has = (r: Requirement) => form.reqs.some((x) => reqKey(x) === reqKey(r));
  const toggle = (r: Requirement) => setForm((f) => ({ ...f, reqs: has(r) ? f.reqs.filter((x) => reqKey(x) !== reqKey(r)) : [...f.reqs, r] }));
  const chip = (r: Requirement, text: string) => (
    <button key={reqKey(r)} type="button" className="chip" aria-pressed={has(r)} onClick={() => toggle(r)}>
      {has(r) && <Check size={14} />}{text}
    </button>
  );

  return (
    <div className="stack lg">
      <div className="form-grid three-up">
        <div className="field">
          <label htmlFor="cat">Product</label>
          <select id="cat" value={form.category} aria-invalid={!!errors.category} onChange={(e) => setForm((f) => ({ ...f, category: e.target.value }))}>
            <option value="">Choose…</option>
            {facets.categories.map((c) => <option key={c.code} value={c.code}>{c.label}</option>)}
          </select>
          {errors.category && <span className="error-text">{errors.category}</span>}
        </div>
        <div className="field">
          <label htmlFor="qty">Quantity</label>
          <div className="input-group">
            <input id="qty" inputMode="decimal" value={form.qty} placeholder="1000" aria-invalid={!!errors.qty}
              onChange={(e) => setForm((f) => ({ ...f, qty: e.target.value.replace(/[^\d.]/g, '') }))} />
            <select aria-label="Unit" value={form.unit} style={{ width: 76 }} onChange={(e) => setForm((f) => ({ ...f, unit: e.target.value as Form['unit'] }))}>
              {(facets.units ?? ['L', 'kg', 'pcs']).map((u) => <option key={u}>{u}</option>)}
            </select>
          </div>
          {errors.qty && <span className="error-text">{errors.qty}</span>}
        </div>
        <div className="field">
          <label htmlFor="date">Deliver by</label>
          <input id="date" type="date" value={form.date} min={isoIn(0)} aria-invalid={!!errors.date} onChange={(e) => setForm((f) => ({ ...f, date: e.target.value }))} />
          {errors.date && <span className="error-text">{errors.date}</span>}
        </div>
      </div>

      <div className="field">
        <span className="label">Requirements</span>
        <div className="chips">
          {facets.claims.map((c) => chip({ kind: 'CLAIM', code: c.code, label: c.label }, c.label))}
          {facets.labels.map((l) => chip({ kind: 'LABEL', code: l.code, label: l.label }, l.label))}
        </div>
      </div>
      <div className="field">
        <span className="label">Free from</span>
        <div className="chips">
          {facets.allergens.map((a) => chip({ kind: 'ALLERGEN_FREE', code: a.code, label: `No ${a.label.toLowerCase()}` }, a.label))}
        </div>
      </div>

      <details className="more">
        <summary>More options <ChevronDown size={15} className="chev" /></summary>
        <div className="form-grid" style={{ marginTop: 14 }}>
          <div className="field">
            <label htmlFor="budget">Budget cap (€)</label>
            <input id="budget" inputMode="decimal" value={form.budget} placeholder="No cap" onChange={(e) => setForm((f) => ({ ...f, budget: e.target.value.replace(/[^\d.]/g, '') }))} />
          </div>
          <div className="field">
            <span className="label">Substitutes</span>
            <Segmented<Sub> label="Substitutes" value={form.substitution}
              options={[['SAME_CATEGORY', 'Similar OK'], ['NONE', 'None'], ['ANY', 'Any']]}
              onChange={(v) => setForm((f) => ({ ...f, substitution: v }))} />
          </div>
        </div>
      </details>
    </div>
  );
}
