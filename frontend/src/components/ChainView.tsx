// "How Aimo chose these options": the agent's four steps, each of which can be retried.
// While a request runs, the steps appear one by one (with the evidence each used); once the options are
// ready the panel folds into a one-line summary above them. Any step can be retried ("Retry") to redo
// it and everything after it, which replays the run from that step.
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check, ChevronDown, ChevronRight, Loader2, RotateCcw, X } from 'lucide-react';
import { getChain, rejectNode } from '../api/endpoints';
import { describeError } from '../api/client';
import type { ChainEvent, ChainNode, ChainTimeline, RejectReason, RequestResult } from '../api/types';
import { NODE_LABEL, REASON, time } from '../lib/format';
import { useFlow } from '../state/flow';
import { useSession } from '../state/session';
import { Button, useDismiss, useToast } from './ui';
import { MascotFace } from './Mascot';

const STEPS = ['UNDERSTAND', 'FILTER', 'RANK', 'GATE'] as const;
const ACTIVITY: Record<string, string> = {
  UNDERSTAND: 'Reading your request',
  FILTER: 'Filtering the catalogue on certified product claims',
  RANK: 'Checking live price and stock, then ranking',
  GATE: 'Checking the best option against your protocol',
};
const SOURCE: Record<string, string> = {
  CATALOG: 'Catalogue',
  BUYER_INPUT: 'Your confirmed form',
  VALIO_PRICE_API: 'Valio price API',
  VALIO_STOCK_API: 'Valio stock API',
  RULE: 'Protocol rule',
};
const WEIGHT_PRESETS: [string, Record<string, number>][] = [
  ['Cheapest first', { price: 0.6, fulfilment: 0.2, carbon: 0.1, local: 0.1 }],
  ['Most reliable first', { price: 0.2, fulfilment: 0.6, carbon: 0.1, local: 0.1 }],
  ['Greener & local first', { price: 0.2, fulfilment: 0.3, carbon: 0.25, local: 0.25 }],
];

type Choice = { label: string; params: Record<string, unknown> };
type StepState = 'done' | 'active' | 'todo' | 'failed';

const reducedMotion = () => typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches;

/** Timeline events carry no node number; match them on their title. */
function eventNode(e: ChainEvent): number {
  const t = e.title.toLowerCase();
  if (/understood|understand/.test(t)) return 1;
  if (/filter/.test(t)) return 2;
  if (/protocol|rule/.test(t)) return 4;
  return 3;
}

export function ChainView({ request }: { request: RequestResult | null }) {
  const { flow, patch } = useFlow();
  const { mode } = useSession();
  const navigate = useNavigate();
  const toast = useToast();
  const run = flow.run;
  const fast = reducedMotion();

  const [done, setDone] = useState(run ? run.from - 1 : STEPS.length);
  const [open, setOpen] = useState(false);
  const [settled, setSettled] = useState(false); // body fully open: let popovers overflow it
  const [openNode, setOpenNode] = useState<number | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [timeline, setTimeline] = useState<ChainTimeline | null>(null);
  const expanded = !!run || open;

  // A new run starts: keep the steps before `from`, redo the rest.
  useEffect(() => {
    if (run?.pending) {
      setDone(run.from - 1);
      setOpen(false);
    }
  }, [run?.pending, run?.from]);

  // Once the answer is in, tick the steps off one at a time, then fold the panel away.
  useEffect(() => {
    if (!run || run.pending || run.error) return;
    if (done < STEPS.length) {
      const t = setTimeout(() => setDone((d) => d + 1), fast ? 80 : done === run.from - 1 ? 420 : 680);
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => patch({ run: null }), fast ? 150 : 900);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, done]);

  // Evidence for each step comes from the timeline endpoint.
  useEffect(() => {
    if (!request || run?.pending) return;
    let live = true;
    setTimeline(null);
    getChain(request.request_id, request.attempt).then((t) => live && setTimeline(t)).catch(() => {});
    return () => { live = false; };
  }, [request, run?.pending]);

  /** Follow-up choices for reasons that need parameters (design doc: reject params). */
  const followUps = (reason: RejectReason): Choice[] | null => {
    if (!request) return null;
    switch (reason) {
      case 'ALLOW_BRAND': {
        const brands = new Set((request.blocked ?? []).flatMap((b) => (b.violations ?? []).filter((v) => v.rule === 'PROFILE_EXCLUDED_BRAND').map(() => b.lines[0].brand ?? '')));
        return [...brands].filter(Boolean).map((b) => ({ label: `Allow ${b} for this request only`, params: { brand: b } }));
      }
      case 'EXCLUDE_PRODUCT':
        return request.candidates.map((c) => ({ label: c.lines[0].name, params: { product_id: c.lines[0].product_id } }));
      case 'EXCLUDE_OPTION':
        return request.candidates.map((c) => ({ label: `#${c.rank} ${c.lines[0].name}`, params: { candidate_id: c.id } }));
      case 'WRONG_PRIORITY':
        return WEIGHT_PRESETS.map(([label, weights]) => ({ label, params: { weights } }));
      default:
        return null;
    }
  };

  const reject = async (node: ChainNode, reason: RejectReason, params: Record<string, unknown> = {}, note?: string) => {
    if (!request) return;
    setOpenNode(null);
    if (node.node === 1) {
      patch({ correcting: reason });
      navigate('/request');
      return;
    }
    if (node.node === 4 && reason === 'PICK_OTHER') {
      patch({ selection: null });
      setOpen(false);
      toast('The protocol check cannot be overridden. Pick another option below.', 'info');
      document.getElementById('options-grid')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }
    if (node.node === 4) {
      setBusy(4);
      try {
        const result = await rejectNode(request.request_id, 4, { reason, params, note: note ?? null });
        patch({ request: result, sentToApproval: true });
        toast(mode === 'SEPARATE' ? 'Sent to the procurement manager' : 'Added to your approval list', 'ok');
        navigate(mode === 'SEPARATE' ? '/orders' : '/approvals');
      } catch (e) {
        toast(describeError(e), 'bad');
      } finally {
        setBusy(null);
      }
      return;
    }
    // Steps ② and ③: replay the run from this step; earlier steps stay as they are.
    patch({ run: { from: node.node, pending: true }, selection: null });
    try {
      const result = await rejectNode(request.request_id, node.node, { reason, params, note: note ?? null });
      patch({ request: result, regenFrom: node.node, run: { from: node.node, pending: false } });
    } catch (e) {
      patch({ run: null });
      toast(describeError(e), 'bad');
    }
  };

  const stateOf = (n: number): StepState => {
    if (n <= done) return 'done';
    if (run && n === done + 1) return run.error ? 'failed' : 'active';
    return 'todo';
  };
  const active = run ? Math.min(done + 1, STEPS.length) : null;

  const head = (() => {
    if (run?.error) return { title: 'Couldn’t finish the search', sub: run.error };
    if (run) {
      const kept = run.from > 1 ? ` · step${run.from > 2 ? 's' : ''} ${Array.from({ length: run.from - 1 }, (_, i) => i + 1).join(', ')} kept` : '';
      return done >= STEPS.length
        ? { title: 'Options ready', sub: `${request?.candidates.length ?? 0} options found${kept}` }
        : { title: run.from > 1 ? `Redoing from step ${run.from}` : 'Finding options for you', sub: `Step ${active} of ${STEPS.length} · ${ACTIVITY[STEPS[active! - 1]]}…${kept}` };
    }
    const n = request?.candidates.length ?? 0;
    const blocked = request?.blocked?.length ?? 0;
    return {
      title: 'How Aimo chose these options',
      sub: `${STEPS.length} steps: understand → filter → rank → check your rules · ${n} option${n === 1 ? '' : 's'}${blocked ? ` · ${blocked} left out` : ''} · not right? Retry any step`,
    };
  })();

  return (
    <section className={`run card ${run ? 'running' : ''} ${run?.error ? 'failed' : ''}`} aria-labelledby="run-title" aria-busy={!!run && !run.error}>
      <button type="button" className="run-head" onClick={() => !run && setOpen((o) => !o)} aria-expanded={expanded}
        aria-controls="run-body" disabled={!!run}>
        <span className="run-face" aria-hidden><MascotFace size={34} mood={run?.error ? 'asking' : run && done < STEPS.length ? 'thinking' : run ? 'happy' : 'idle'} /></span>
        <span className="grow">
          <span id="run-title" className="run-title">{head.title}</span>
          <span className="run-sub" aria-live="polite">{head.sub}</span>
        </span>
        <span className="run-progress" aria-hidden>
          {STEPS.map((s, i) => <i key={s} className={stateOf(i + 1)} />)}
        </span>
        {!run && <span className="run-toggle">{open ? 'Hide steps' : 'Show steps'}<ChevronDown size={16} className="chev" /></span>}
      </button>

      <div id="run-body" className={`run-body ${expanded ? 'open' : ''} ${expanded && settled ? 'settled' : ''}`}
        onTransitionEnd={(e) => e.target === e.currentTarget && setSettled(expanded)}>
        <div className="run-inner">
          <ol className="run-steps">
            {STEPS.map((name, i) => {
              const n = i + 1;
              const node = request?.chain.find((c) => c.node === n);
              const st = stateOf(n);
              const events = (timeline?.events ?? []).filter((e) => eventNode(e) === n);
              const regen = flow.regenFrom != null && n >= flow.regenFrom;
              return (
                <li key={name} className={`run-step ${st}`}>
                  <span className="run-mark" aria-hidden>
                    {st === 'done' ? <Check size={14} strokeWidth={3} /> : st === 'active' ? <Loader2 size={15} className="spin" /> : st === 'failed' ? <X size={14} strokeWidth={3} /> : n}
                  </span>
                  <div className="run-content">
                    <div className="run-name-row">
                      <span className="run-name">{NODE_LABEL[name]}</span>
                      {regen && st === 'done' && <span className="run-updated">Updated</span>}
                      {st === 'done' && !run && node && (
                        <NotRight n={node} open={openNode === n} busy={busy === n}
                          onToggle={() => setOpenNode(openNode === n ? null : n)} onClose={() => setOpenNode(null)}
                          followUps={followUps} onReject={(r, p, note) => reject(node, r, p, note)} />
                      )}
                    </div>
                    {st === 'active' && <p className="run-activity">{ACTIVITY[name]}<span className="run-dots" aria-hidden><i /><i /><i /></span></p>}
                    {st === 'failed' && <p className="run-activity bad">Stopped here.</p>}
                    {st === 'done' && node && (
                      <div className="run-detail">
                        <p className="run-summary">{node.summary}</p>
                        {events.map((e, j) => (
                          <div key={j} className="run-evidence" title={time(e.at)}>
                            <span className="run-logic">{e.logic}</span>
                            <span className="evidence">
                              {(e.evidence ?? []).map((v, k) => {
                                const raw = v.detail.trim().startsWith('{');
                                const label = SOURCE[v.source] ?? v.source.replace(/_/g, ' ').toLowerCase();
                                return (
                                  <span key={k} className={`src ${v.trust_grade === 'A' ? 'verified' : ''}`}>
                                    <b>{label}</b>{!raw && <> · {v.detail}</>}{v.trust_grade && <> · grade {v.trust_grade}</>}
                                  </span>
                                );
                              })}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>

          {run?.error ? (
            <div className="run-foot">
              <Button variant="primary" onClick={() => { patch({ run: null }); navigate('/request'); }}>Back to request</Button>
            </div>
          ) : !run && (
            <p className="run-foot small muted">Want something different? Retry any step and only that step onward is redone.</p>
          )}
        </div>
      </div>
    </section>
  );
}

function NotRight({ n, open, busy, onToggle, onClose, followUps, onReject }: {
  n: ChainNode; open: boolean; busy: boolean;
  onToggle: () => void; onClose: () => void;
  followUps: (r: RejectReason) => Choice[] | null;
  onReject: (r: RejectReason, params?: Record<string, unknown>, note?: string) => void;
}) {
  const close = useCallback(() => onClose(), [onClose]);
  const ref = useDismiss(open, close);
  const [sub, setSub] = useState<RejectReason | null>(null);
  const [note, setNote] = useState('');
  const outOfRetries = n.retries_left <= 0;
  const isGate = n.node === 4;

  return (
    <div className="run-reject" ref={ref}>
      {!isGate && n.retries_left <= 1 && <span className="xs muted">{n.retries_left} retr{n.retries_left === 1 ? 'y' : 'ies'} left</span>}
      <Button size="sm" variant="ghost" className="adjust" onClick={() => { setSub(null); onToggle(); }} loading={busy}
        disabled={!n.can_reject || outOfRetries} aria-haspopup="menu" aria-expanded={open}
        title={outOfRetries ? 'Retried 3 times already. Ask for human approval instead.' : undefined}>
        <RotateCcw size={14} aria-hidden /> Retry
      </Button>
      {open && (
        <div className="popover" role="menu" aria-label={`Retry step ${n.node}`}>
          {!sub ? (
            <>
              <div className="menu-note">{isGate ? 'Rules can’t be overridden. You can:' : 'What should change?'}</div>
              {n.reject_options.map((r) => {
                const reason = r as RejectReason;
                const needs = followUps(reason) || reason === 'OTHER';
                return (
                  <button key={r} role="menuitem" className="menu-item" onClick={() => (needs && n.node !== 1 ? setSub(reason) : onReject(reason))}>
                    {REASON[r] ?? r}
                    {needs && n.node !== 1 && <ChevronRight size={14} className="chev" />}
                  </button>
                );
              })}
            </>
          ) : sub === 'OTHER' ? (
            <form className="stack sm" style={{ padding: 6 }} onSubmit={(e) => { e.preventDefault(); if (note.trim()) onReject('OTHER', {}, note); }}>
              <label className="label" htmlFor={`note-${n.node}`}>Tell the agent what to change</label>
              <textarea id={`note-${n.node}`} rows={2} value={note} onChange={(e) => setNote(e.target.value)} autoFocus />
              <div className="row"><Button size="sm" variant="ghost" type="button" onClick={() => setSub(null)}>Back</Button><Button size="sm" variant="primary" className="ml-auto">Redo step</Button></div>
            </form>
          ) : (
            <>
              <button className="menu-item muted" onClick={() => setSub(null)}>← {REASON[sub]}</button>
              {(followUps(sub) ?? []).length === 0 && <div className="menu-note">Nothing to choose from here.</div>}
              {(followUps(sub) ?? []).map((c) => (
                <button key={c.label} role="menuitem" className="menu-item" onClick={() => onReject(sub, c.params)}>{c.label}</button>
              ))}
              {sub === 'ALLOW_BRAND' && <div className="menu-note">Only for this request; your profile is not changed.</div>}
            </>
          )}
        </div>
      )}
    </div>
  );
}
