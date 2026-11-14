// Step 1. One flow, not two competing paths:
//   type what you need → the AI fills in the form → check / adjust → find options.
// The form can also be filled by hand without typing anything.
// When the AI is unsure about something, the form shows an empty slot for it, the AI asks in its reply,
// and the chosen answer visibly flies into that slot: the question exists to complete the form.
import { useEffect, useMemo, useRef, useState, type CSSProperties, type RefObject } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, ArrowUp, Check, ChevronDown, WandSparkles } from 'lucide-react';
import { Mascot, MascotFace, type MascotMood } from '../components/Mascot';
import { createRequest, getFacets, listProfiles, parseRequest, rejectNode } from '../api/endpoints';
import { describeError } from '../api/client';
import type { Ambiguity, Facets, Requirement, StructuredRequest } from '../api/types';
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
/** A short form label for what the AI asked about. */
const clarifyLabel = (q: Ambiguity) => (/whole|skim/i.test(q.question) ? 'Milk type' : q.question.replace(/\?$/, ''));
const reducedMotion = () => typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches;

const formFrom = (s: StructuredRequest): Form => ({
  category: s.category,
  reqs: s.requirements ?? [],
  qty: String(s.quantity.value),
  unit: s.quantity.unit,
  date: s.delivery_date,
  budget: s.budget_cents ? String(s.budget_cents / 100) : '',
  substitution: s.substitution ?? 'SAME_CATEGORY',
});

interface Flight { text: string; from: DOMRect; to: DOMRect }

/** Example prompts: one click fills the message box, which teaches what A.I.mo can read. */
const EXAMPLES = [
  '1000 litres of lactose-free milk by Monday',
  'Nut-free cream for 200 desserts, Friday',
  'Gluten-free pasta for the ward',
];

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
  const [flight, setFlight] = useState<Flight | null>(null);
  const [landed, setLanded] = useState(false);
  const [parsing, setParsing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [tried, setTried] = useState(false);
  const [aiFilled, setAiFilled] = useState(false);
  const slotRef = useRef<HTMLDivElement>(null);

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

  const question = flow.correcting ? undefined : flow.parse?.ambiguities?.[0];
  const errors = useMemo(() => {
    const e: Partial<Record<'category' | 'qty' | 'date', string>> = {};
    if (!form.category) e.category = 'Choose a category';
    if (!(Number(form.qty) > 0)) e.qty = 'Enter a quantity';
    if (!form.date) e.date = 'Choose a date';
    else if (form.date < isoIn(0)) e.date = 'Date is in the past';
    return e;
  }, [form]);
  const blocker = question && !answer ? `Choose the ${clarifyLabel(question).toLowerCase()} first` : Object.values(errors)[0];

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

  /** The answer lands in the form: the slot fills and lights up briefly. */
  const commit = (o: string) => {
    setAnswer(o);
    setLanded(true);
    setTimeout(() => setLanded(false), 1100);
  };

  /** Picking an answer in the AI's reply sends a copy of the chip down into its slot in the form. */
  const pick = (o: string, chip: HTMLElement) => {
    const slot = slotRef.current;
    if (!slot || reducedMotion()) return commit(o);
    setFlight({ text: o, from: chip.getBoundingClientRect(), to: slot.getBoundingClientRect() });
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
      notes: question && answer && answer !== 'Any' ? `${clarifyLabel(question)}: ${answer}` : null,
    };
    const fromText = !!flow.parse;
    const correcting = flow.correcting;
    const prev = flow.request;
    // Go straight to the options page: it shows the agent working through the steps while this call runs.
    setSubmitting(true);
    patch({
      run: { from: 1, pending: true }, request: correcting ? prev : null, panel: structured,
      regenFrom: correcting ? 1 : null, correcting: null,
      selection: null, order: null, sentToApproval: false, approvalResult: null,
    });
    navigate('/options');
    try {
      const request = correcting && prev
        ? await rejectNode(prev.request_id, 1, { reason: correcting, params: { structured } })
        : await createRequest({
            protocol_id: workspace.protocolId!,
            profile_id: profile?.id ?? null,
            input_mode: fromText ? 'NATURAL_LANGUAGE' : 'PANEL',
            raw_text: fromText ? flow.rawText : null,
            structured,
          });
      patch({ request, run: { from: 1, pending: false } });
    } catch (e) {
      patch({ run: { from: 1, pending: false, error: describeError(e) }, correcting });
    } finally {
      setSubmitting(false);
    }
  };

  const mood: MascotMood = parsing ? 'thinking' : question && !answer ? 'asking' : flow.parse ? 'happy' : 'idle';

  return (
    <div className="narrow">
      {flow.correcting && <PageHead title="Correct your request" />}

      {flow.correcting ? (
        <div style={{ marginBottom: 16 }}>
          <Banner tone="info" title="Fix the details below"
            action={<Button size="sm" variant="ghost" onClick={() => { patch({ correcting: null }); navigate('/options'); }}>Cancel</Button>}>
            The options are found again from here on.
          </Banner>
        </div>
      ) : (
        <>
          {/* ── Hero: A.I.mo + message box + examples ── */}
          <section className="hero" aria-labelledby="hero-title">
          <span className="hero-mascot"><Mascot mood={mood} size={150} /></span>
          <div>
            <h1 id="hero-title" className="display hero-title">What does your kitchen need?</h1>
            <p className="hero-sub">Tell A.I.mo in your own words. It fills in the order form for you to check.</p>
          </div>
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
                {!parsing && <WandSparkles size={16} />} Fill in form
              </Button>
            </div>
          </div>
          <div className="examples">
            <span className="small strong label">Try</span>
            {EXAMPLES.map((ex) => (
              <button key={ex} type="button" className="chip example" onClick={() => patch({ rawText: ex, parse: null })}>{ex}</button>
            ))}
          </div>
          </section>

          {flow.parse && (
            <AiReply question={question} answer={answer} inFlight={!!flight} onPick={pick} highlight={tried && !!question && !answer} />
          )}
        </>
      )}

      {/* ── The form ── */}
      <section className={`card form-card ${aiFilled ? 'ai-filled' : ''}`} aria-label="Request details">
        {facets.loading ? <Skeleton h={300} /> : facets.error ? <ErrorState error={facets.error} onRetry={facets.reload} /> : (
          <DetailsForm facets={facets.data!} form={form} setForm={setForm} errors={tried ? errors : {}}
            clarify={question && { question, answer, setAnswer: commit, slotRef, landed, needed: tried && !answer, hidden: !!flight }} />
        )}
      </section>

      <div className="form-actions">
        {tried && blocker && <span className="small" style={{ color: 'var(--bad)' }}>{blocker}</span>}
        <Button variant="primary" size="lg" className="ml-auto" loading={submitting} onClick={submit}>
          Find options <ArrowRight size={17} />
        </Button>
      </div>

      {flight && (
        <FlyingChip flight={flight} onDone={() => {
          commit(flight.text);
          setFlight(null);
        }} />
      )}
    </div>
  );
}

function AiReply({ question, answer, inFlight, onPick, highlight }: {
  question?: Ambiguity; answer: string | null; inFlight: boolean; onPick: (o: string, el: HTMLElement) => void; highlight: boolean;
}) {
  const answered = !!question && !!answer && !inFlight;
  return (
    <div className={`ai-reply ${question && !answered ? 'asking' : ''}`} aria-live="polite">
      <span className="ai-avatar" aria-hidden><MascotFace mood={question && !answered ? 'asking' : 'happy'} /></span>
      <div className="stack sm grow">
        {!question && <p>I’ve filled in the details below. Check them, then find options.</p>}
        {question && (answered ? (
          <p className="ai-done">
            <Check size={16} className="ok-ink" aria-hidden /> Got it. <b>{answer}</b> is in the form. Check the details, then find options.
          </p>
        ) : (
          <p>I’ve filled in the form below. Choose one more thing to finish it:</p>
        ))}
        {question && (
          // Stays mounted so it can fold away smoothly once the answer has landed in the form.
          <div className={`fold ${answered ? 'closed' : ''}`} aria-hidden={answered}>
            <div className={`stack sm ${highlight ? 'needs-answer' : ''}`}>
              <p className="ai-question">{question.question}</p>
              <div className="chips" role="radiogroup" aria-label={question.question}>
                {question.options?.map((o) => (
                  <button key={o} type="button" className="chip" role="radio" aria-checked={false} disabled={inFlight || answered}
                    tabIndex={answered ? -1 : undefined} onClick={(e) => onPick(o, e.currentTarget)}>
                    {o}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/** A copy of the picked chip that travels from the AI's reply into its slot in the form, then hands over. */
function FlyingChip({ flight, onDone }: { flight: Flight; onDone: () => void }) {
  const { from, to, text } = flight;
  const style = {
    left: from.left,
    top: from.top,
    height: from.height,
    '--dx': `${to.left + 6 - from.left}px`,
    '--dy': `${to.top + (to.height - from.height) / 2 - from.top}px`,
  } as CSSProperties;
  return (
    <div className="fly-chip" style={style} onAnimationEnd={onDone} aria-hidden>
      <Check size={14} /> {text}
    </div>
  );
}

function DetailsForm({ facets, form, setForm, errors, clarify }: {
  facets: Facets; form: Form; setForm: (fn: (f: Form) => Form) => void; errors: Partial<Record<'category' | 'qty' | 'date', string>>;
  clarify?: { question: Ambiguity; answer: string | null; setAnswer: (v: string) => void; slotRef: RefObject<HTMLDivElement>; landed: boolean; needed: boolean; hidden: boolean };
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

      {clarify && <ClarifyField {...clarify} />}

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

/** The form's slot for what the AI asked about: empty until answered, then a normal, editable field. */
function ClarifyField({ question, answer, setAnswer, slotRef, landed, needed, hidden }: {
  question: Ambiguity; answer: string | null; setAnswer: (v: string) => void; slotRef: RefObject<HTMLDivElement>; landed: boolean; needed: boolean; hidden: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const label = clarifyLabel(question);
  const options = (question.options ?? []).map((o) => [o, o] as [string, string]);

  return (
    <div className="field">
      <span className="label">{label}</span>
      {answer && editing ? (
        <Segmented<string> label={label} value={answer} options={options} onChange={(v) => { setAnswer(v); setEditing(false); }} />
      ) : (
        <div ref={slotRef} className={`slot ${answer && !hidden ? 'filled' : ''} ${landed ? 'landed' : ''} ${needed ? 'needed' : ''}`}>
          {answer && !hidden ? (
            <>
              <span className="slot-value"><Check size={14} /> {answer}</span>
              <button type="button" className="btn ghost sm" onClick={() => setEditing(true)}>Change</button>
            </>
          ) : (
            <span className="slot-empty"><ArrowUp size={15} /> Answer the question above</span>
          )}
        </div>
      )}
    </div>
  );
}
