// Step 0. Before an agent may order anything, the buyer and Valio sign a protocol: what the agent may buy, how much
// it may spend on its own, and who approves the rest. A workspace without an active protocol lands here instead of
// on its pages: describe the rules → A.I.mo drafts them → check → sign → Valio confirms → ordering opens.
import { useEffect, useState } from 'react';
import { Check, PenLine, ShieldCheck, Sparkles } from 'lucide-react';
import { draftProtocol, getFacets, listProtocols, signProtocol } from '../api/endpoints';
import { describeError } from '../api/client';
import type { Protocol } from '../api/types';
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
    setBusy('draft');
    try {
      setDraft(await draftProtocol(workspace.buyerId!, workspace.agentId!, text));
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setBusy(null);
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
            <Button variant="primary" onClick={make} loading={busy === 'draft'} disabled={!text.trim()}>
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
          <div className="kv">
            <div className="k">May buy</div><div>{shown.body.allowed_categories.map(category).join(', ')}</div>
            <div className="k">Always</div><div>{(shown.body.hard_requirements ?? []).map((r) => r.label ?? r.code).join(', ') || 'No hard requirements'}</div>
            <div className="k">Orders on its own</div><div>up to {eur(shown.body.auto_approve_limit_cents)}</div>
            <div className="k">Needs approval</div><div>{eur(shown.body.auto_approve_limit_cents)} – {eur(shown.body.max_order_cents)}, by {person(shown.body.approver)}{shown.body.approval_mode === 'SELF' ? ' (logged as a separate step)' : ''}</div>
            <div className="k">Never</div><div>above {eur(shown.body.max_order_cents)}</div>
            <div className="k">Valid until</div><div>{shown.body.valid_until}</div>
          </div>
          {!waiting && (shown.open_questions?.length ?? 0) > 0 && (
            <div className="stack sm">
              {shown.open_questions!.map((q) => (
                <Banner key={q.field} tone="info" title={q.question}>
                  {q.suggested != null && <>Using the suggestion for now; change it later on the Protocol page.</>}
                </Banner>
              ))}
            </div>
          )}
          {!waiting ? (
            <div className="row">
              <Button variant="primary" onClick={sign} loading={busy === 'sign'}><PenLine size={15} /> Sign as {account.name}</Button>
              <Button variant="ghost" onClick={() => setDraft(null)}>Change the description</Button>
            </div>
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
