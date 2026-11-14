// The current purchase journey inside one buyer workspace: request → options → quote → (approval) → order.
// Shared by every account in the workspace so the chef → manager hand-off is visible on both sides.
import { createContext, useContext, useMemo, useState, type ReactNode } from 'react';
import type { ApprovalDecisionResult, Candidate, Order, Protocol, ParseResult, RejectReason, RequestResult, SelectResult, StructuredRequest } from '../api/types';

export interface Selection {
  candidate: Candidate;
  result: SelectResult;
  expiresAt: number; // epoch ms
  idempotencyKey: string; // reused on retries so a timeout never creates two orders
}

export interface FlowState {
  rawText: string;
  parse: ParseResult | null;
  /** Panel values, either typed by the user or pre-filled from the AI's understanding. */
  panel: StructuredRequest | null;
  request: RequestResult | null;
  regenFrom: number | null;
  /** The agent is working: steps before `from` are kept, `from` onward are being (re)done.
   *  `pending` while the API call is in flight; the options page reveals the steps, then clears this. */
  run: { from: number; pending: boolean; error?: string } | null;
  /** Step ① was marked "not right" with this reason: the request page is in correction mode. */
  correcting: RejectReason | null;
  selection: Selection | null;
  order: Order | null;
  /** Step ④ "send to human approval" without an order yet. */
  sentToApproval: boolean;
  approvalResult: 'APPROVED' | 'REJECTED' | 'REQUOTE' | null;
  /** Decisions taken in this session, by approval id (the M1 fixture server does not remember them). */
  decisions: Record<string, ApprovalDecisionResult>;
  /** A protocol draft the buyer signed; Valio confirms it from the review queue. */
  signedDraft: Protocol | null;
}

const initial: FlowState = {
  rawText: '1000 litres of lactose-free milk by Monday',
  parse: null,
  panel: null,
  request: null,
  regenFrom: null,
  run: null,
  correcting: null,
  selection: null,
  order: null,
  sentToApproval: false,
  approvalResult: null,
  decisions: {},
  signedDraft: null,
};

interface FlowCtx {
  flow: FlowState;
  patch: (p: Partial<FlowState>) => void;
  reset: () => void;
}

const Ctx = createContext<FlowCtx | null>(null);

export function FlowProvider({ children }: { children: ReactNode }) {
  const [flow, setFlow] = useState<FlowState>(initial);
  const value = useMemo<FlowCtx>(
    () => ({ flow, patch: (p) => setFlow((f) => ({ ...f, ...p })), reset: () => setFlow(initial) }),
    [flow],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useFlow() {
  const c = useContext(Ctx);
  if (!c) throw new Error('useFlow outside FlowProvider');
  return c;
}

export type StepState = 'done' | 'current' | 'todo' | 'blocked' | 'skipped';
export interface Step {
  key: string;
  label: string;
  hint: string;
  state: StepState;
  page?: 'request' | 'options' | 'approvals' | 'orders';
}

/** Progress shown in the sidebar. Derived only from flow state, never stored.
 *  Hints appear only once a step has happened, so the list stays quiet. */
export function progressSteps(f: FlowState, mode: 'SEPARATE' | 'SELF'): Step[] {
  const described = !!f.request;
  const chosen = !!f.order || f.sentToApproval;
  const waiting = f.sentToApproval || f.order?.status === 'PENDING_BUYER_APPROVAL';
  const placed = f.order?.status === 'PLACED';
  const rejected = f.approvalResult === 'REJECTED' || f.order?.status === 'REJECTED';
  const approvedByHuman = f.approvalResult === 'APPROVED';
  const autoPlaced = placed && !approvedByHuman;
  const approver = mode === 'SELF' ? 'you' : 'the manager';
  const q = f.request?.structured;

  return [
    { key: 'describe', label: 'Describe the need', page: 'request',
      hint: q ? `${q.category_label ?? 'Request'} · ${q.quantity.value.toLocaleString('en')} ${q.quantity.unit}` : '',
      state: described ? 'done' : 'current' },
    { key: 'options', label: 'Choose an option', page: 'options',
      hint: chosen && f.selection ? f.selection.candidate.lines[0].name : '',
      state: chosen ? 'done' : described ? 'current' : 'todo' },
    { key: 'approval', label: 'Approval', page: 'approvals',
      hint: autoPlaced ? 'Not needed' : rejected ? 'Rejected' : f.approvalResult === 'REQUOTE' ? 'Price changed' : approvedByHuman ? `Approved by ${approver}` : waiting ? `Waiting for ${approver}` : '',
      state: rejected ? 'blocked' : autoPlaced ? 'skipped' : approvedByHuman ? 'done' : waiting ? 'current' : 'todo' },
    { key: 'placed', label: 'Ordered', page: 'orders', hint: '', state: placed ? 'done' : 'todo' },
  ];
}
