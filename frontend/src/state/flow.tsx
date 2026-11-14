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

/** Progress shown in the sidebar. Derived only from flow state, never stored. */
export function progressSteps(f: FlowState, mode: 'SEPARATE' | 'SELF'): Step[] {
  const gate = f.selection?.result.gate.outcome;
  const needsApproval = f.sentToApproval || gate === 'BUYER_APPROVAL' || gate === 'VALIO_REVIEW' || f.order?.status === 'PENDING_BUYER_APPROVAL';
  const placed = f.order?.status === 'PLACED';
  const rejected = f.approvalResult === 'REJECTED' || f.order?.status === 'REJECTED';
  const approver = mode === 'SELF' ? 'you' : 'the manager';

  const s = (cond: boolean, prev: boolean): StepState => (cond ? 'done' : prev ? 'current' : 'todo');
  const describe = !!f.request;
  const reviewed = !!f.selection || f.sentToApproval;
  const submitted = !!f.order || f.sentToApproval;

  const steps: Step[] = [
    { key: 'describe', label: 'Describe the need', hint: describe ? `${f.request!.structured.category_label ?? 'Request'} · ${f.request!.structured.quantity.value.toLocaleString('en')} ${f.request!.structured.quantity.unit}` : 'Words or the panel', state: describe ? 'done' : 'current', page: 'request' },
    { key: 'options', label: 'Choose an option', hint: reviewed && f.selection ? f.selection.candidate.lines[0].name : `Up to 8 ranked options`, state: s(reviewed, describe), page: 'options' },
    { key: 'quote', label: 'Signed quote', hint: f.selection ? 'Valid 15 minutes' : 'Locked price & date', state: s(submitted, reviewed), page: 'options' },
  ];
  steps.push({
    key: 'approval',
    label: 'Approval',
    hint: !needsApproval && submitted ? 'Not needed: within protocol' : rejected ? 'Rejected' : f.approvalResult === 'REQUOTE' ? 'Price changed: re-confirm' : f.approvalResult === 'APPROVED' ? `Approved by ${approver}` : needsApproval ? `Waiting for ${approver}` : 'Only if outside the protocol',
    state: rejected ? 'blocked' : !needsApproval && submitted ? 'skipped' : f.approvalResult === 'APPROVED' ? 'done' : needsApproval ? 'current' : 'todo',
    page: 'approvals',
  });
  steps.push({ key: 'placed', label: 'Order placed', hint: placed ? f.order!.id : 'Re-checked before placing', state: placed ? 'done' : 'todo', page: 'orders' });
  return steps;
}
