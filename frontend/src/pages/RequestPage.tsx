// Step 1. Two equal ways to ask, each finds options on its own:
//   - tell A.I.mo in your own words: it reads the message and finds options straight away; when something it
//     must know is missing (product, quantity, date, an unknown requirement) it asks, and your answer goes on;
//   - or fill in the form: product, quantity and date are required, everything else is optional.
// What A.I.mo understood is mirrored into the form, so the two never disagree and you can adjust it there.
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, ArrowUp, Check, ChevronDown, RotateCcw, ShieldCheck } from 'lucide-react';
import { Mascot, MascotFace, type MascotMood } from '../components/Mascot';
import { createRequest, getActiveProtocol, getFacets, listProfiles, parseRequest, rejectNode } from '../api/endpoints';
import { describeError } from '../api/client';
import type { Ambiguity, Facets, RequestResult, Requirement, StructuredRequest } from '../api/types';
import { useSession } from '../state/session';
import { useFlow } from '../state/flow';
import { useActiveProtocol } from '../state/protocol';
import { ACCOUNTS, PAGES_BY_ROLE } from '../config/accounts';
import { eur } from '../lib/format';
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
/** What A.I.mo must know before it can look for options; anything else it asks about keeps a sensible default. */
const BLOCKING = new Set(['category', 'quantity', 'delivery_date', 'requirements']);
/** The message plus the answers to A.I.mo's questions, as the parser reads them (one "field: answer" per line). */
const withAnswers = (message: string, answers: Record<string, string>) =>
  message + Object.entries(answers).map(([field, answer]) => `\n${field}: ${answer}`).join('');

const formFrom = (s: StructuredRequest): Form => ({
  category: s.category,
  reqs: s.requirements ?? [],
  qty: String(s.quantity.value),
  unit: s.quantity.unit,
  date: s.delivery_date,
  budget: s.budget_cents ? String(s.budget_cents / 100) : '',
  substitution: s.substitution ?? 'SAME_CATEGORY',
});



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

  const [form, setFormState] = useState<Form>(() => (flow.panel ? formFrom(flow.panel) : EMPTY));
  /** 'ai' while the form shows exactly what A.I.mo understood; any hand edit makes it the user's own. */
  const [formSource, setFormSource] = useState<'ai' | 'you'>('you');
  const setForm = (fn: (f: Form) => Form) => { setFormState(fn); setFormSource('you'); };

  const [input, setInput] = useState(flow.rawText);
  const [message, setMessage] = useState<string | null>(null);      // the request being worked out with A.I.mo
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [asking, setAsking] = useState<Ambiguity | null>(null);
  const [stuck, setStuck] = useState<Ambiguity | null>(null);         // asked, answered, still unreadable
  const [busy, setBusy] = useState<'chat' | 'form' | null>(null);
  const [tried, setTried] = useState(false);
  const [aiFilled, setAiFilled] = useState(false);

  // Profile defaults go into empty fields only.
  useEffect(() => {
    const d = profile?.body.defaults;
    if (!d || flow.panel) return;
    setFormState((f) => ({ ...f, substitution: d.substitution ?? f.substitution, date: f.date || (d.lead_days != null ? isoIn(d.lead_days) : '') }));
  }, [profile, flow.panel]);

  // "Retry" on step ①: start from what the agent understood.
  useEffect(() => {
    if (flow.correcting && flow.request) setFormState(formFrom(flow.request.structured));
  }, [flow.correcting, flow.request]);

  const errors = useMemo(() => {
    const e: Partial<Record<'category' | 'qty' | 'date', string>> = {};
    if (!form.category) e.category = 'Choose a product';
    if (!(Number(form.qty) > 0)) e.qty = 'Enter a quantity';
    if (!form.date) e.date = 'Choose a date';
    else if (form.date < isoIn(0)) e.date = 'Date is in the past';
    return e;
  }, [form]);
  const blocker = Object.values(errors)[0];

  /** Go straight to the options page, which shows the agent working through the steps while this call runs. */
  const run = async (structured: StructuredRequest, call: () => Promise<RequestResult>, correcting = flow.correcting) => {
    const prev = flow.request;
    patch({
      run: { from: 1, pending: true }, request: correcting ? prev : null, panel: structured,
      regenFrom: correcting ? 1 : null, correcting: null,
      selection: null, order: null, sentToApproval: false, approvalResult: null,
    });
    navigate('/options');
    try {
      patch({ request: await call(), run: { from: 1, pending: false } });
    } catch (e) {
      patch({ run: { from: 1, pending: false, error: describeError(e) }, correcting });
    }
  };

  /** Run the four steps on a new request. */
  const find = (structured: StructuredRequest, rawText: string | null) => run(structured, async () => {
    const protocol = await getActiveProtocol(workspace.agentId!);
    return createRequest({
      protocol_id: protocol.id,
      profile_id: profile?.id ?? null,
      input_mode: rawText ? 'NATURAL_LANGUAGE' : 'PANEL',
      raw_text: rawText,
      structured,
    });
  }, null);

  /** Way 1: a message to A.I.mo, or the answer to its question. */
  const send = async (reply?: string) => {
    const typed = (reply ?? input).trim();
    if (!typed || busy) return;
    const answering = asking && message !== null ? asking : null;
    const msg = answering ? message! : typed;
    const ans = answering ? { ...answers, [answering.field]: typed } : {};
    const text = withAnswers(msg, ans);
    setMessage(msg);
    setAnswers(ans);
    setInput('');
    setStuck(null);
    patch({ rawText: msg });
    setBusy('chat');
    try {
      const parse = await parseRequest(text, profile?.id);
      patch({ parse });
      setFormState(formFrom(parse.structured));
      setFormSource('ai');
      setAiFilled(true);
      setTimeout(() => setAiFilled(false), 1600);
      const open = parse.ambiguities.find((a) => BLOCKING.has(a.field));
      if (open && answering && open.field === answering.field) {
        setAsking(null);            // the answer did not help: hand over to the form instead of asking again
        setStuck(open);
        return;
      }
      if (open) {
        setAsking(open);
        return;
      }
      setAsking(null);
      find(parse.structured, text);
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setBusy(null);
    }
  };

  const startOver = () => {
    setMessage(null);
    setAnswers({});
    setAsking(null);
    setStuck(null);
    setInput('');
    patch({ parse: null });
  };

  /** Way 2: the form, as filled in by hand or as A.I.mo left it. */
  const submitForm = async () => {
    setTried(true);
    if (blocker || busy) return;
    const cat = facets.data?.categories.find((c) => c.code === form.category);
    const structured: StructuredRequest = {
      category: form.category,
      category_label: cat?.label ?? null,
      requirements: form.reqs,
      quantity: { value: Number(form.qty), unit: form.unit },
      delivery_date: form.date,
      budget_cents: form.budget ? Math.round(Number(form.budget) * 100) : null,
      substitution: form.substitution,
      notes: null,
    };
    if (flow.correcting && flow.request) {
      const { request_id } = flow.request, reason = flow.correcting;
      run(structured, () => rejectNode(request_id, 1, { reason, params: { structured } }));
    } else {
      // Untouched since A.I.mo filled it: the request still comes from the message, so keep the words with it.
      find(structured, formSource === 'ai' && message !== null ? withAnswers(message, answers) : null);
    }
  };

  const mood: MascotMood = busy === 'chat' ? 'thinking' : asking ? 'asking' : formSource === 'ai' ? 'happy' : 'idle';

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
          <MandateStrip />
          {/* ── Way 1: tell A.I.mo ── */}
          <section className="hero" aria-labelledby="hero-title">
          <span className="hero-mascot"><Mascot mood={mood} size={150} /></span>
          <div>
            <h1 id="hero-title" className="display hero-title">What does your kitchen need?</h1>
            <p className="hero-sub">Tell A.I.mo in your own words and it finds the options. Or fill in the form below.</p>
          </div>
          <div className="composer">
            <textarea
              aria-label={asking ? asking.question : 'Describe what you need'}
              rows={2}
              value={input}
              placeholder={asking ? `Answer: ${asking.question}` : 'e.g. 1000 litres of lactose-free milk by Monday'}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  send();
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
              <Button variant="primary" size="sm" className="ml-auto" loading={busy === 'chat'} disabled={!input.trim() || !!busy} onClick={() => send()}>
                {asking ? <>Answer <ArrowUp size={16} /></> : <>Find options <ArrowRight size={16} /></>}
              </Button>
            </div>
          </div>
          {message === null && (
            <div className="examples">
              <span className="small strong label">Try</span>
              {EXAMPLES.map((ex) => (
                <button key={ex} type="button" className="chip example" onClick={() => setInput(ex)}>{ex}</button>
              ))}
            </div>
          )}
          </section>

          {message !== null && (
            <AiReply message={message} answers={answers} asking={asking} stuck={stuck} thinking={busy === 'chat'}
              onPick={(o) => send(o)} onStartOver={startOver} />
          )}
        </>
      )}

      {/* ── Way 2: the form ── */}
      <section className={`card form-card ${aiFilled ? 'ai-filled' : ''}`} aria-labelledby="form-title">
        <h2 id="form-title" style={{ marginBottom: 'var(--s4)' }}>{flow.correcting ? 'Your request' : 'Order form'}</h2>
        {facets.loading ? <Skeleton h={300} /> : facets.error ? <ErrorState error={facets.error} onRetry={facets.reload} /> : (
          <DetailsForm facets={facets.data!} form={form} setForm={setForm} errors={tried ? errors : {}} />
        )}
      </section>

      <div className="form-actions">
        {tried && blocker && <span className="small" style={{ color: 'var(--bad)' }}>{blocker}</span>}
        <Button variant={message === null || flow.correcting ? 'primary' : 'default'} size="lg" className="ml-auto" loading={busy === 'form'} onClick={submitForm}>
          Find options <ArrowRight size={17} />
        </Button>
      </div>
    </div>
  );
}

/** Step 0 at a glance: the signed protocol every request below is checked against. */
function MandateStrip() {
  const { protocol } = useActiveProtocol();
  const { account } = useSession();
  if (!protocol) return null;
  const b = protocol.body;
  const approver = ACCOUNTS.find((a) => a.userId === b.approver);
  const approves = !approver ? `${b.approver} approves` : approver.userId === account.userId ? 'you approve' : `${approver.name} approves`;
  const canView = PAGES_BY_ROLE[account.key].some((p) => p.key === 'protocol');
  return (
    <div className="mandate-strip" role="note" aria-label="Your authorisation">
      <span className="mandate-step"><ShieldCheck size={16} aria-hidden /> Step 0 done · protocol v{protocol.version}, signed and confirmed by Valio</span>
      <span className="mandate-terms">
        Up to {eur(b.auto_approve_limit_cents)} orders straight away · {eur(b.auto_approve_limit_cents)}–{eur(b.max_order_cents)} {approves} · over {eur(b.max_order_cents)} not allowed
      </span>
      {canView && <a href="#/protocol">View or change</a>}
    </div>
  );
}

/** The exchange with A.I.mo: your message, your answers, and what it is doing or asking. */
function AiReply({ message, answers, asking, stuck, thinking, onPick, onStartOver }: {
  message: string; answers: Record<string, string>; asking: Ambiguity | null; stuck: Ambiguity | null; thinking: boolean;
  onPick: (o: string) => void; onStartOver: () => void;
}) {
  return (
    <div className={`ai-reply ${asking ? 'asking' : ''}`} aria-live="polite">
      <span className="ai-avatar" aria-hidden><MascotFace mood={thinking ? 'thinking' : asking || stuck ? 'asking' : 'happy'} /></span>
      <div className="stack sm grow">
        <p className="small muted">You: “{message}”{Object.values(answers).map((a) => ` · “${a}”`).join('')}</p>
        {thinking && <p>Reading your request and looking for options…</p>}
        {!thinking && asking && (
          <>
            <p>I need one more thing before I can look:</p>
            <p className="ai-question">{asking.question}</p>
            {(asking.options?.length ?? 0) > 0 && (
              <div className="chips" role="group" aria-label={asking.question}>
                {asking.options!.map((o) => (
                  <button key={o} type="button" className="chip" onClick={() => onPick(o)}>{o}</button>
                ))}
              </div>
            )}
            <p className="small muted">Pick one, type your answer above, or set it in the form and find options there.</p>
          </>
        )}
        {!thinking && stuck && (
          <p>I still couldn’t work out “{stuck.question.replace(/\?$/, '')}”. Set it in the form below and find options there.</p>
        )}
        {!thinking && !asking && !stuck && (
          <p className="ai-done"><Check size={16} className="ok-ink" aria-hidden /> I’ve filled in the form below.</p>
        )}
        <div>
          <button type="button" className="btn ghost sm" onClick={onStartOver}><RotateCcw size={14} /> Start over</button>
        </div>
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
        <span className="label">Requirements <span className="muted small">optional</span></span>
        <div className="chips">
          {facets.claims.map((c) => chip({ kind: 'CLAIM', code: c.code, label: c.label }, c.label))}
          {facets.labels.map((l) => chip({ kind: 'LABEL', code: l.code, label: l.label }, l.label))}
        </div>
      </div>
      <div className="field">
        <span className="label">Free from <span className="muted small">optional</span></span>
        <div className="chips">
          {facets.allergens.map((a) => chip({ kind: 'ALLERGEN_FREE', code: a.code, label: `No ${a.label.toLowerCase()}` }, a.label))}
        </div>
      </div>

      <details className="more">
        <summary>More options <span className="muted small">optional</span> <ChevronDown size={15} className="chev" /></summary>
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
