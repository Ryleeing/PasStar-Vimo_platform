// Step 1. Two equal ways in, side by side:
//   left  — the order form, filled in by hand;
//   right — a chat with A.I.mo: describe the order in your own words and it writes it into the form.
// Both end in the same form, which is what gets submitted. When A.I.mo is unsure about something, the form
// shows an empty slot for it, A.I.mo asks in the chat, and the chosen answer visibly flies into that slot.
import { useEffect, useMemo, useRef, useState, type CSSProperties, type RefObject } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, ArrowUp, Check, ChevronDown, MessageCircle } from 'lucide-react';
import { Mascot, MascotFace, type MascotMood } from '../components/Mascot';
import { createRequest, getFacets, listProfiles, parseRequest, rejectNode } from '../api/endpoints';
import { describeError } from '../api/client';
import type { Ambiguity, Facets, Requirement, StructuredRequest } from '../api/types';
import { dayLabel, num } from '../lib/format';
import { useSession } from '../state/session';
import { useFlow } from '../state/flow';
import { Banner, Button, ErrorState, PageHead, Segmented, Skeleton, useAsync } from '../components/ui';

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

/** "1,000 L lactose-free milk, by Mon 16 Nov" */
function summarize(s: StructuredRequest, facets: Facets | null) {
  const cat = s.category_label ?? facets?.categories.find((c) => c.code === s.category)?.label ?? 'products';
  const reqs = (s.requirements ?? []).map((r) => (r.label ?? r.code).toLowerCase()).join(', ');
  return `${num(s.quantity.value)} ${s.quantity.unit} ${reqs ? `${reqs} ` : ''}${cat.toLowerCase()}, by ${dayLabel(s.delivery_date)}`;
}

interface Flight { text: string; from: DOMRect; to: DOMRect }
type Reply = { state: 'pending' } | { state: 'done'; summary: string } | { state: 'error'; error: string };
interface Turn { id: number; text: string; reply: Reply }

/** Example messages: one click puts it in the message box, which shows what A.I.mo can read. */
const EXAMPLES = [
  '1000 litres of lactose-free milk by Monday',
  'Nut-free cream for 200 desserts, Friday',
  'Gluten-free pasta for the ward',
];

export function RequestPage() {
  const { workspace } = useSession();
  const { flow, patch } = useFlow();
  const navigate = useNavigate();

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
  const [draft, setDraft] = useState('');
  // Coming back to this page: show the last exchange again.
  const [turns, setTurns] = useState<Turn[]>(() => (flow.parse && flow.rawText
    ? [{ id: 1, text: flow.rawText, reply: { state: 'done', summary: summarize(flow.parse.structured, null) } }] : []));
  const slotRef = useRef<HTMLDivElement>(null);

  // Profile defaults go into empty fields only.
  useEffect(() => {
    const d = profile?.body.defaults;
    if (!d || flow.panel) return;
    setForm((f) => ({ ...f, substitution: d.substitution ?? f.substitution, date: f.date || (d.lead_days != null ? isoIn(d.lead_days) : '') }));
  }, [profile, flow.panel]);

  // "Adjust" on step ①: start from what the agent understood.
  useEffect(() => {
    if (flow.correcting && flow.request) setForm(formFrom(flow.request.structured));
  }, [flow.correcting, flow.request]);

  const question = flow.correcting ? undefined : flow.parse?.ambiguities?.[0];
  const errors = useMemo(() => {
    const e: Partial<Record<'category' | 'qty' | 'date', string>> = {};
    if (!form.category) e.category = 'Choose a product';
    if (!(Number(form.qty) > 0)) e.qty = 'Enter a quantity';
    if (!form.date) e.date = 'Choose a date';
    else if (form.date < isoIn(0)) e.date = 'Date is in the past';
    return e;
  }, [form]);
  const blocker = question && !answer ? `Choose the ${clarifyLabel(question).toLowerCase()} first` : Object.values(errors)[0];

  /** Send a message to A.I.mo: it reads it and writes the order into the form. */
  const send = async (text = draft) => {
    const t = text.trim();
    if (!t || parsing) return;
    const id = Date.now();
    setTurns((ts) => [...ts, { id, text: t, reply: { state: 'pending' } }]);
    setDraft('');
    setParsing(true);
    // A follow-up ("make it 800 litres") is read together with what was said before.
    const full = turns.length && flow.parse ? `${flow.rawText}. ${t}` : t;
    const prevQuestion = question?.question;
    const prevAnswer = answer;
    patch({ rawText: full });
    try {
      const parse = await parseRequest(full, profile?.id);
      patch({ parse });
      // Keep an answer already given if A.I.mo would ask the same thing again.
      setAnswer(parse.ambiguities?.[0]?.question === prevQuestion ? prevAnswer : null);
      setForm(formFrom(parse.structured));
      setTurns((ts) => ts.map((x) => (x.id === id ? { ...x, reply: { state: 'done', summary: summarize(parse.structured, facets.data ?? null) } } : x)));
      setAiFilled(true);
      setTimeout(() => setAiFilled(false), 1600);
    } catch (e) {
      setTurns((ts) => ts.map((x) => (x.id === id ? { ...x, reply: { state: 'error', error: describeError(e) } } : x)));
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

  /** Picking an answer in the chat sends a copy of the chip across into its slot in the form. */
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
  const profilePick = profiles.data && profiles.data.length > 0 && (
    <label className="profile-pick">
      <span>Profile</span>
      <select value={profile?.id ?? ''} onChange={(e) => setProfileId(e.target.value)}>
        {profiles.data.map((p) => <option key={p.id} value={p.id}>{p.body.name}</option>)}
      </select>
    </label>
  );

  return (
    <div className="req-page">
      {flow.correcting ? (
        <>
          <PageHead title="Adjust your request" />
          <div style={{ marginBottom: 16 }}>
            <Banner tone="info" title="Change the details below"
              action={<Button size="sm" variant="ghost" onClick={() => { patch({ correcting: null }); navigate('/options'); }}>Cancel</Button>}>
              The options are found again from here on.
            </Banner>
          </div>
        </>
      ) : (
        <header className="req-head">
          <div>
            <h1 className="display req-title">What does your kitchen need?</h1>
            <p className="req-sub">Fill in the order yourself, or describe it to A.I.mo and it writes it in for you.</p>
          </div>
          {profilePick}
        </header>
      )}

      <div className={`req-layout ${flow.correcting ? 'solo' : ''}`}>
        {/* ── left: the order form ── */}
        <div className="req-form">
          <section className={`card form-card ${aiFilled ? 'ai-filled' : ''}`} aria-labelledby="order-title">
            <header className="form-head">
              <h2 id="order-title">Your order</h2>
              {flow.parse && !flow.correcting && <span className="form-src"><MascotFace size={22} mood="happy" /> Written in by A.I.mo · edit anything</span>}
            </header>
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
        </div>

        {/* ── right: describe it to A.I.mo ── */}
        {!flow.correcting && (
          <aside className="chat" aria-label="Describe your order to A.I.mo">
           <div className="chat-inner">
            <header className="chat-head">
              <span className="chat-mascot" aria-hidden><Mascot mood={mood} size={64} /></span>
              <div>
                <h2>Ask A.I.mo</h2>
                <p>Describe it in your own words. It goes straight into the form.</p>
              </div>
            </header>
            <Thread turns={turns} question={question} answer={answer} inFlight={!!flight} onPick={pick}
              highlight={tried && !!question && !answer} onExample={(ex) => { setDraft(ex); }} />
            <div className="chat-compose">
              <textarea aria-label="Message to A.I.mo" rows={2} value={draft}
                placeholder={turns.length ? 'Change something, e.g. “make it 800 litres”' : 'e.g. 1000 litres of lactose-free milk by Monday'}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }} />
              <button type="button" className="send" onClick={() => send()} disabled={!draft.trim() || parsing} aria-label="Send to A.I.mo">
                <ArrowUp size={18} />
              </button>
            </div>
           </div>
          </aside>
        )}
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

/** The conversation: a greeting with examples, then each message and A.I.mo's reply. */
function Thread({ turns, question, answer, inFlight, onPick, highlight, onExample }: {
  turns: Turn[]; question?: Ambiguity; answer: string | null; inFlight: boolean;
  onPick: (o: string, el: HTMLElement) => void; highlight: boolean; onExample: (ex: string) => void;
}) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: 'nearest', behavior: reducedMotion() ? 'auto' : 'smooth' }); }, [turns, answer]);
  const last = turns[turns.length - 1];
  const answered = !!question && !!answer && !inFlight;

  return (
    <div className="chat-body" aria-live="polite">
      <div className="bubble ai">
        <span className="bubble-face" aria-hidden><MascotFace size={26} /></span>
        <div className="bubble-text">
          <p>Hi! Tell me what you need and when. I’ll put it into the order for you to check.</p>
          {!turns.length && (
            <div className="chat-examples">
              {EXAMPLES.map((ex) => <button key={ex} type="button" className="chip example" onClick={() => onExample(ex)}>{ex}</button>)}
            </div>
          )}
        </div>
      </div>

      {turns.map((t) => {
        const isLast = t === last;
        return (
          <div key={t.id} className="turn">
            <div className="bubble user"><div className="bubble-text"><p>{t.text}</p></div></div>
            <div className={`bubble ai ${isLast && question && !answered && t.reply.state === 'done' ? 'asking' : ''}`}>
              <span className="bubble-face" aria-hidden>
                <MascotFace size={26} mood={t.reply.state === 'pending' ? 'thinking' : isLast && question && !answered ? 'asking' : 'happy'} />
              </span>
              <div className="bubble-text">
                {t.reply.state === 'pending' && <p className="typing" aria-label="A.I.mo is reading"><i /><i /><i /></p>}
                {t.reply.state === 'error' && <p className="bad-text">I couldn’t read that: {t.reply.error}</p>}
                {t.reply.state === 'done' && (
                  <>
                    <p>I’ve put this into the order: <b>{t.reply.summary}</b>.</p>
                    {isLast && question && (
                      <div className={`fold ${answered ? 'closed' : ''}`} aria-hidden={answered}>
                        <div className={`stack sm ${highlight ? 'needs-answer' : ''}`}>
                          <p>One thing to finish it:</p>
                          <p className="ai-question">{question.question}</p>
                          <div className="chips" role="radiogroup" aria-label={question.question}>
                            {question.options?.map((o) => (
                              <button key={o} type="button" className="chip" role="radio" aria-checked={false} disabled={inFlight || answered}
                                tabIndex={answered ? -1 : undefined} onClick={(e) => onPick(o, e.currentTarget)}>{o}</button>
                            ))}
                          </div>
                        </div>
                      </div>
                    )}
                    {isLast && (!question || answered) && (
                      <p className="ai-done">
                        <Check size={15} className="ok-ink" aria-hidden />
                        {answered ? <>Got it, <b>{answer}</b>. Check the order, then find options.</> : <>Check the order, then find options.</>}
                      </p>
                    )}
                  </>
                )}
              </div>
            </div>
          </div>
        );
      })}
      <div ref={end} />
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
            <span className="slot-empty"><MessageCircle size={15} /> Answer A.I.mo’s question</span>
          )}
        </div>
      )}
    </div>
  );
}
