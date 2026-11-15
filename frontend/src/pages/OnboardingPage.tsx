// Step 0. Before an agent may order anything, the buyer and Valio sign a protocol: what the agent may buy, how much
// it may spend on its own, and who approves the rest. A workspace without an active protocol lands here instead of
// on its pages: describe the rules → Aimo drafts them → check → sign → Valio confirms → ordering opens.
import { useEffect, useState } from 'react';
import { Bot, Check, Pencil, PenLine, ShieldCheck, Sparkles, Store, Truck } from 'lucide-react';
import { draftProtocol, getFacets, listProtocols, signProtocol, updateProtocol } from '../api/endpoints';
import { describeError } from '../api/client';
import type { Protocol, ProtocolBody } from '../api/types';
import { ProtocolEditor } from '../components/ProtocolEditor';
import { ACCOUNTS } from '../config/accounts';
import { Mascot } from '../components/Mascot';
import { Banner, Button, useAsync, useToast } from '../components/ui';
import { eur } from '../lib/format';
import { useSession } from '../state/session';
import { useActiveProtocol } from '../state/protocol';

const SUGGESTED = 'Milk, cream and milk powder. Order on your own up to 1,000 euros, never above 3,000 euros. '
  + 'I approve anything in between myself.';
const STAGES = ['Describe your rules', 'Check what Aimo wrote', 'Sign them', 'Valio confirms'] as const;
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
  const [checkedAt, setCheckedAt] = useState<string | null>(null);
  const reload = active.reload;
  useEffect(() => {
    if (!waiting) return;
    const t = setInterval(() => { reload(); setCheckedAt(new Date().toLocaleTimeString('en-GB')); }, WAIT_POLL_MS);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [waiting?.id]);

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
          <p className="eyebrow">Step 0 · one-time setup, before the first order</p>
          <h1 id="onboard-title" className="display hero-title">Set the rules for your AI ordering assistant</h1>
          <p className="hero-sub">
            You are {account.name}, the {account.title.toLowerCase()} of {workspace.name}, a restaurant that buys its food from
            Valio Aimo. An AI assistant can do the ordering for you, here or in ChatGPT. First you decide what it may buy
            and how much it may spend without asking you. Think of it as giving it a company card with a limit.
          </p>
        </div>
      </section>

      <section className="who-grid" aria-label="Who is involved">
        <div className="who card">
          <span className="who-icon" aria-hidden><Store size={20} /></span>
          <b>You · {workspace.name}</b>
          <span className="small muted">Write the rules once, and approve the bigger orders yourself.</span>
        </div>
        <div className="who card">
          <span className="who-icon" aria-hidden><Bot size={20} /></span>
          <b>Your AI assistant</b>
          <span className="small muted">Finds products and orders for you, but only within your rules.</span>
        </div>
        <div className="who card">
          <span className="who-icon" aria-hidden><Truck size={20} /></span>
          <b>Valio Aimo · the supplier</b>
          <span className="small muted">Confirms the rules too, and refuses any order that breaks them.</span>
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
            <h2 id="describe-title">What may your assistant do?</h2>
            <div className="small muted">Say it in your own words: what it may buy, how much it may spend alone, and what it must never do. Aimo writes it up as a protocol (the formal rules), and you check it before anything is signed.</div>
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
            <ProtocolEditor body={draft.body} facets={facets.data} onSave={save} onCancel={() => setEditing(false)} />
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
            <Banner tone="info" title="Signed. Now Valio has to confirm."
              action={valio && <Button size="sm" onClick={() => switchAccount(valio.key)}><ShieldCheck size={15} /> Switch to {valio.name} (Valio)</Button>}>
              In real life Valio would review your rules within a day. In this demo, switch to {valio?.name ?? 'Valio'}, Valio’s reviewer,
              read the rules and confirm them. When you come back, ordering is open.
              <span className="wait-line">
                <span className="run-dots" aria-hidden><i /><i /><i /></span> Checking for Valio’s answer every few seconds
                {checkedAt && <> · last checked {checkedAt}</>}
                <button type="button" className="btn ghost sm" onClick={() => { active.reload(); setCheckedAt(new Date().toLocaleTimeString('en-GB')); }}>Check now</button>
              </span>
            </Banner>
          )}
        </section>
      )}
    </div>
  );
}
