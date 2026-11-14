// Step 0. Before an agent may order anything, the buyer and Valio sign a protocol: what the agent may buy, how much
// it may spend on its own, and who approves the rest. A workspace without an active protocol lands here instead of
// on its pages: describe the rules → A.I.mo drafts them → check → sign → Valio confirms → ordering opens.
import { useEffect, useState } from 'react';
import { Check, Pencil, PenLine, ShieldCheck, Sparkles } from 'lucide-react';
import { draftProtocol, getFacets, listProtocols, signProtocol, updateProtocol } from '../api/endpoints';
import { describeError } from '../api/client';
import type { Facets, Protocol, ProtocolBody, Requirement } from '../api/types';
import { ACCOUNTS } from '../config/accounts';
import { Mascot } from '../components/Mascot';
import { Banner, Button, useAsync, useToast } from '../components/ui';
import { eur } from '../lib/format';
import { useSession } from '../state/session';
import { useActiveProtocol } from '../state/protocol';

const SUGGESTED = 'Milk, cream and milk powder. Order on your own up to 1,000 euros, never above 3,000 euros. '
  + 'I approve anything in between myself.';
const STAGES = ['Describe the rules', 'Check the draft', 'Sign', 'Valio confirms'] as const;
const WAIT_POLL_MS = 3000;

export function OnboardingPage() {
  const { workspace, account, switchAccount } = useSession();
  const active = useActiveProtocol();
  const toast = useToast();
  const facets = useAsync(getFacets, []);
  // A protocol signed earlier (e.g. before a page reload) is still waiting for Valio: pick up from there.
  const pending = useAsync(() => listProtocols('PENDING_VALIO_CONFIRMATION'), [account.key]);
  const [text, setText] = useState(SUGGESTED);
  const [draft, setDraft] = useState<Protocol | null>(null);
  const [busy, setBusy] = useState<'draft' | 'sign' | null>(null);
  const [editing, setEditing] = useState(false);
  const [edited, setEdited] = useState(false);

  const waiting = draft?.status === 'PENDING_VALIO_CONFIRMATION' ? draft
    : (pending.data ?? []).find((p) => p.body.agent_id === workspace.agentId) ?? null;
  const shown = waiting ?? draft;
  const stage = waiting ? 3 : draft ? 1 : 0;

  // While Valio has not confirmed, look again every few seconds; the gate opens as soon as it has.
  useEffect(() => {
    if (!waiting) return;
    const t = setInterval(() => active.reload(), WAIT_POLL_MS);
    return () => clearInterval(t);
  }, [waiting, active]);

  const make = async () => {
    if (!text.trim() || busy) return;
    setBusy('draft');
    try {
      setDraft(await draftProtocol(workspace.buyerId!, workspace.agentId!, text));
      setEdited(false);
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setBusy(null);
    }
  };
  /** Edits go straight into the draft (PUT /v1/protocols/{id}); only a DRAFT can be edited, and only before signing. */
  const save = async (body: ProtocolBody) => {
    if (!draft) return;
    try {
      setDraft(await updateProtocol(draft.id, body));
      setEditing(false);
      setEdited(true);
      toast('Draft updated', 'ok');
    } catch (e) {
      toast(describeError(e), 'bad');
    }
  };

  const sign = async () => {
    if (!draft) return;
    setBusy('sign');
    try {
      setDraft(await signProtocol(draft.id));
      toast('Signed. Valio confirms it next.', 'ok');
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setBusy(null);
    }
  };

  const category = (code: string) => facets.data?.categories.find((c) => c.code === code)?.label ?? code;
  const person = (userId: string) => {
    const a = ACCOUNTS.find((x) => x.userId === userId);
    return a ? (a.userId === account.userId ? `you (${a.name})` : `${a.name}, ${a.title}`) : userId;
  };
  const valio = ACCOUNTS.find((a) => a.workspace === 'valio');

  return (
    <div className="narrow stack lg">
      <section className="hero" aria-labelledby="onboard-title">
        <span className="hero-mascot"><Mascot mood={waiting ? 'thinking' : draft ? 'happy' : 'idle'} size={130} /></span>
        <div>
          <p className="eyebrow">Step 0 · before the first order</p>
          <h1 id="onboard-title" className="display hero-title">Authorise your purchasing agent</h1>
          <p className="hero-sub">
            {workspace.name} and Valio sign one protocol: what your agent may buy, how much it may order on its own,
            and who approves the rest. Every order, from this page or from ChatGPT, is checked against it.
          </p>
        </div>
      </section>

      <ol className="onboard-stages" aria-label="Authorisation steps">
        {STAGES.map((s, i) => (
          <li key={s} className={i < stage ? 'done' : i === stage ? 'current' : 'todo'}>
            <span className="step-dot">{i < stage ? <Check size={14} strokeWidth={3} /> : i + 1}</span> {s}
          </li>
        ))}
      </ol>

      {stage === 0 && (
        <section className="card stack" aria-labelledby="describe-title">
          <div>
            <h2 id="describe-title">Describe the rules in your own words</h2>
            <div className="small muted">A.I.mo turns them into a protocol. You check it before anything is signed.</div>
          </div>
          <div className="field">
            <textarea id="rules-text" aria-label="Describe the rules" rows={3} value={text} onChange={(e) => setText(e.target.value)} />
          </div>
          <div className="row">
            <Button variant="primary" onClick={make} loading={busy === 'draft'}>
              <Sparkles size={15} /> Draft the protocol
            </Button>
          </div>
        </section>
      )}

      {shown && (
        <section className="card stack" aria-labelledby="draft-title">
          <div className="row">
            <h2 id="draft-title" className="grow">{waiting ? 'Signed protocol' : 'Draft protocol'}</h2>
            <span className="small muted">{shown.id} · version {shown.version}</span>
          </div>
          {editing && draft && !waiting && facets.data ? (
            <DraftEditor body={draft.body} facets={facets.data} onSave={save} onCancel={() => setEditing(false)} />
          ) : (
          <div className="kv">
            <div className="k">May buy</div><div>{shown.body.allowed_categories.map(category).join(', ')}</div>
            <div className="k">Always</div><div>{(shown.body.hard_requirements ?? []).map((r) => r.label ?? r.code).join(', ') || 'No hard requirements'}</div>
            <div className="k">Orders on its own</div><div>up to {eur(shown.body.auto_approve_limit_cents)}</div>
            <div className="k">Needs approval</div><div>{eur(shown.body.auto_approve_limit_cents)} – {eur(shown.body.max_order_cents)}, by {person(shown.body.approver)}{shown.body.approval_mode === 'SELF' ? ' (logged as a separate step)' : ''}</div>
            <div className="k">Never</div><div>above {eur(shown.body.max_order_cents)}</div>
            <div className="k">Valid until</div><div>{shown.body.valid_until}</div>
          </div>
          )}
          {!waiting && !editing && !edited && (shown.open_questions?.length ?? 0) > 0 && (
            <div className="stack sm">
              {shown.open_questions!.map((q) => (
                <Banner key={q.field} tone="info" title={q.question}>
                  {q.suggested != null && <>Using the suggestion for now. Use “Edit” to change it.</>}
                </Banner>
              ))}
            </div>
          )}
          {!waiting ? (
            !editing && (
              <div className="row wrap">
                <Button variant="primary" onClick={sign} loading={busy === 'sign'}><PenLine size={15} /> Sign as {account.name}</Button>
                <Button onClick={() => setEditing(true)} disabled={!facets.data}><Pencil size={15} /> Edit</Button>
                <Button variant="ghost" onClick={() => setDraft(null)}>Describe again</Button>
              </div>
            )
          ) : (
            <Banner tone="info" title="Signed. Waiting for Valio to confirm."
              action={valio && <Button size="sm" onClick={() => switchAccount(valio.key)}><ShieldCheck size={15} /> Switch to {valio.name} (Valio)</Button>}>
              Valio checks the customer and sets its own limits for agents, then signs. Ordering opens here as soon as it has.
            </Banner>
          )}
        </section>
      )}
    </div>
  );
}

/** The draft's terms as a form, so the buyer fixes what A.I.mo got wrong without describing everything again. */
function DraftEditor({ body, facets, onSave, onCancel }: {
  body: ProtocolBody; facets: Facets; onSave: (b: ProtocolBody) => Promise<void>; onCancel: () => void;
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
        <Button variant="primary" onClick={save} loading={saving} disabled={!!problem}>Save changes</Button>
        <Button variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}
