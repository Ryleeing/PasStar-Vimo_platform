import { useCallback, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { getChain, rejectNode } from '../api/endpoints';
import { describeError } from '../api/client';
import type { ChainNode, ChainTimeline, RejectReason, RequestResult } from '../api/types';
import { NODE_LABEL, REASON, time } from '../lib/format';
import { useFlow } from '../state/flow';
import { useSession } from '../state/session';
import { Button, Pill, Skeleton, useDismiss, useToast } from './ui';

const CIRCLED = ['①', '②', '③', '④'];
const WEIGHT_PRESETS: [string, Record<string, number>][] = [
  ['Cheapest first', { price: 0.6, fulfilment: 0.2, carbon: 0.1, local: 0.1 }],
  ['Most reliable first', { price: 0.2, fulfilment: 0.6, carbon: 0.1, local: 0.1 }],
  ['Greener & local first', { price: 0.2, fulfilment: 0.3, carbon: 0.25, local: 0.25 }],
];

type Choice = { label: string; params: Record<string, unknown> };

export function ChainView({ request }: { request: RequestResult }) {
  const { flow, patch } = useFlow();
  const { mode } = useSession();
  const navigate = useNavigate();
  const toast = useToast();
  const [openNode, setOpenNode] = useState<number | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [timeline, setTimeline] = useState<ChainTimeline | null>(null);
  const [showTimeline, setShowTimeline] = useState(false);
  const [loadingTl, setLoadingTl] = useState(false);

  const toggleTimeline = async () => {
    const next = !showTimeline;
    setShowTimeline(next);
    if (next) {
      setLoadingTl(true);
      try {
        setTimeline(await getChain(request.request_id, request.attempt));
      } catch (e) {
        toast(describeError(e), 'bad');
      } finally {
        setLoadingTl(false);
      }
    }
  };

  /** Follow-up choices for reasons that need parameters (design doc: reject params). */
  const followUps = (reason: RejectReason): Choice[] | null => {
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
    setOpenNode(null);
    if (node.node === 1) {
      patch({ correcting: reason });
      navigate('/request');
      return;
    }
    if (node.node === 4 && reason === 'PICK_OTHER') {
      patch({ selection: null });
      toast('The protocol check cannot be overridden. Pick another option below.', 'info');
      document.getElementById('options-grid')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }
    setBusy(node.node);
    try {
      const result = await rejectNode(request.request_id, node.node, { reason, params, note: note ?? null });
      if (node.node === 4) {
        patch({ request: result, sentToApproval: true });
        toast(mode === 'SEPARATE' ? 'Sent to the procurement manager' : 'Added to your approval list', 'ok');
        navigate(mode === 'SEPARATE' ? '/orders' : '/approvals');
        return;
      }
      patch({ request: result, regenFrom: node.node, selection: null });
      setTimeline(null);
      setShowTimeline(false);
      toast(`Regenerated from step ${CIRCLED[node.node - 1]} (${REASON[reason]}). Earlier steps unchanged.`, 'ok');
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="card" aria-labelledby="chain-title">
      <div className="row wrap" style={{ marginBottom: 14 }}>
        <div className="grow">
          <h2 id="chain-title">How these options were found</h2>
          <div className="small muted">If a step is wrong, tell us and only that step onward is redone.</div>
        </div>
        <Button variant="ghost" size="sm" onClick={toggleTimeline} aria-expanded={showTimeline}>
          {showTimeline ? <ChevronDown size={15} /> : <ChevronRight size={15} />} Timeline
        </Button>
      </div>

      <div className="chain">
        {request.chain.map((n, i) => (
          <NodeCard key={n.node} n={n} last={i === request.chain.length - 1}
            regen={flow.regenFrom != null && n.node >= flow.regenFrom}
            open={openNode === n.node} busy={busy === n.node}
            onToggle={() => setOpenNode(openNode === n.node ? null : n.node)}
            onClose={() => setOpenNode(null)}
            followUps={followUps} onReject={(r, p, note) => reject(n, r, p, note)} />
        ))}
      </div>

      {showTimeline && (
        <div style={{ marginTop: 24 }}>
          {loadingTl || !timeline ? <Skeleton h={160} /> : (
            <ol className="timeline">
              {timeline.events.map((e, i) => (
                <li key={i}>
                  <span className="t">{time(e.at)}</span>
                  <div className="body">
                    <div className="strong">{e.title}</div>
                    <div className="small text-2">{e.logic}</div>
                    <div className="evidence">
                      {(e.evidence ?? []).map((v, j) => (
                        <Pill key={j} tone={v.trust_grade === 'A' ? 'ok' : 'neutral'}>
                          {v.source.replace(/_/g, ' ').toLowerCase()} · {v.detail}{v.trust_grade ? ` · grade ${v.trust_grade}` : ''}
                        </Pill>
                      ))}
                    </div>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </section>
  );
}

function NodeCard({ n, last, regen, open, busy, onToggle, onClose, followUps, onReject }: {
  n: ChainNode; last: boolean; regen: boolean; open: boolean; busy: boolean;
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
    <div className={`node ${regen ? 'regen' : ''}`}>
      {!last && <span className="node-connector" aria-hidden />}
      <div className="node-head">
        <span className="node-num">{n.node}</span>
        <span className="grow">{NODE_LABEL[n.name]}</span>
        {regen && <span className="xs" style={{ color: 'var(--accent)' }}>Updated</span>}
      </div>
      <p className="node-summary">{n.summary}</p>
      <div className="node-foot" ref={ref}>
        <Button size="sm" variant="ghost" className="danger" onClick={() => { setSub(null); onToggle(); }} loading={busy}
          disabled={!n.can_reject || outOfRetries} aria-haspopup="menu" aria-expanded={open}
          title={outOfRetries ? 'Retried 3 times. Send it to a human instead.' : undefined}>
          Not right?
        </Button>
        {!isGate && n.retries_left <= 1 && <span className="xs muted ml-auto">{n.retries_left} retr{n.retries_left === 1 ? 'y' : 'ies'} left</span>}
        {open && (
          <div className="popover" role="menu" aria-label={`Why is step ${n.node} wrong?`}>
            {!sub ? (
              <>
                <div className="menu-note">{isGate ? 'Rules can’t be overridden. You can:' : 'What’s wrong?'}</div>
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
              <form className="stack sm" style={{ padding: 6 }} onSubmit={(e) => { e.preventDefault(); onReject('OTHER', {}, note); }}>
                <label className="label" htmlFor={`note-${n.node}`}>Tell the agent what to change</label>
                <textarea id={`note-${n.node}`} rows={2} value={note} onChange={(e) => setNote(e.target.value)} autoFocus />
                <div className="row"><Button size="sm" variant="ghost" type="button" onClick={() => setSub(null)}>Back</Button><Button size="sm" variant="primary" disabled={!note.trim()} className="ml-auto">Redo step</Button></div>
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
    </div>
  );
}
