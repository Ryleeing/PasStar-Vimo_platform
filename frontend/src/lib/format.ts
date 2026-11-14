import type { Level, Outcome } from '../api/types';

export const eur = (cents?: number | null) =>
  cents == null ? '—' : '€' + (cents / 100).toLocaleString('en', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const num = (v: number) => v.toLocaleString('en');

export const time = (iso: string) =>
  new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

export const dayLabel = (isoDate: string) =>
  new Date(isoDate + 'T12:00:00').toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });

export const mmss = (s: number) => `${Math.floor(Math.max(0, s) / 60)}:${String(Math.max(0, s) % 60).padStart(2, '0')}`;

export type Tone = 'ok' | 'warn' | 'bad' | 'info' | 'neutral' | 'accent';

export const LEVEL: Record<string, { label: string; tone: Tone }> = {
  HIGH: { label: 'High', tone: 'ok' },
  MEDIUM: { label: 'Medium', tone: 'warn' },
  LOW: { label: 'Low', tone: 'bad' },
  UNKNOWN: { label: 'Unknown', tone: 'neutral' },
};

export const levelOf = (l?: Level | 'UNKNOWN' | null) => LEVEL[l ?? 'UNKNOWN'] ?? LEVEL.UNKNOWN;

export const outcomeOf = (o: Outcome, mode: 'SEPARATE' | 'SELF' = 'SEPARATE'): { label: string; tone: Tone } =>
  ({
    AUTO: { label: 'Orders automatically', tone: 'ok' as Tone },
    BUYER_APPROVAL: { label: mode === 'SELF' ? 'Needs your approval' : 'Needs manager approval', tone: 'warn' as Tone },
    VALIO_REVIEW: { label: 'Needs Valio review', tone: 'info' as Tone },
    REJECT: { label: 'Not allowed', tone: 'bad' as Tone },
  })[o];

export const AVAILABILITY: Record<string, { label: string; tone: Tone }> = {
  YES: { label: 'Deliverable', tone: 'ok' },
  PARTIAL: { label: 'Partly deliverable', tone: 'warn' },
  NO: { label: 'Not deliverable', tone: 'bad' },
  UNKNOWN: { label: 'Availability unknown', tone: 'neutral' },
};

export const BADGE: Record<string, string> = { RECOMMENDED: 'Recommended', CHEAPEST: 'Cheapest', MOST_RELIABLE: 'Most reliable' };

export const ALLERGEN_STATUS: Record<string, { label: string; tone: Tone }> = {
  CLAIMED: { label: 'Declared', tone: 'ok' },
  FREE_FROM: { label: 'Declared free from', tone: 'ok' },
  MAY_CONTAIN: { label: 'May contain', tone: 'bad' },
  CONTAINS: { label: 'Contains', tone: 'bad' },
  UNDECLARED: { label: 'Not declared', tone: 'warn' },
  NOT_CLAIMED: { label: 'Not claimed', tone: 'warn' },
};

export const NODE_LABEL: Record<string, string> = {
  UNDERSTAND: 'Understand request',
  FILTER: 'Filter products',
  RANK: 'Combine & rank',
  GATE: 'Protocol check',
};

export const REASON: Record<string, string> = {
  WRONG_QUANTITY: 'Change the quantity',
  WRONG_DATE: 'Change the delivery date',
  WRONG_ATTRIBUTES: 'Change the requirements',
  WRONG_CATEGORY: 'Change the product type',
  TOO_STRICT: 'Include more products',
  TOO_LOOSE: 'Be stricter',
  ALLOW_BRAND: 'Allow a brand this time',
  EXCLUDE_PRODUCT: 'Leave out a product',
  WRONG_PRIORITY: 'Rank by other priorities',
  EXCLUDE_OPTION: 'Leave out an option',
  PREFER_SINGLE_SKU: 'One product only, no split',
  SEND_TO_APPROVAL: 'Ask for human approval',
  PICK_OTHER: 'Pick another option',
  OTHER: 'Something else',
};

export const ORDER_STATUS: Record<string, { label: string; tone: Tone }> = {
  PLACED: { label: 'Placed', tone: 'ok' },
  PENDING_BUYER_APPROVAL: { label: 'Waiting for approval', tone: 'warn' },
  BUYER_AUTHORIZATION_REQUIRED: { label: 'Needs external authorization', tone: 'warn' },
  PENDING_VALIO_REVIEW: { label: 'Valio is reviewing', tone: 'info' },
  REQUOTE_REQUIRED: { label: 'Price changed: re-confirm', tone: 'warn' },
  REJECTED: { label: 'Rejected', tone: 'bad' },
  CANCELLED: { label: 'Cancelled', tone: 'neutral' },
};

export const APPROVAL_STATUS: Record<string, { label: string; tone: Tone }> = {
  PENDING: { label: 'Pending', tone: 'warn' },
  APPROVED: { label: 'Approved', tone: 'ok' },
  APPROVED_WITH_CHANGES: { label: 'Approved with changes', tone: 'ok' },
  REJECTED: { label: 'Rejected', tone: 'bad' },
  ROUTED: { label: 'Routed', tone: 'info' },
};

export const PROTOCOL_STATUS: Record<string, { label: string; tone: Tone }> = {
  DRAFT: { label: 'Draft', tone: 'neutral' },
  PENDING_BUYER_SIGNATURE: { label: 'Waiting for buyer signature', tone: 'warn' },
  PENDING_VALIO_CONFIRMATION: { label: 'Waiting for Valio', tone: 'info' },
  ACTIVE: { label: 'Active', tone: 'ok' },
  REVOKED: { label: 'Revoked', tone: 'bad' },
  EXPIRED: { label: 'Expired', tone: 'bad' },
  SUPERSEDED: { label: 'Superseded', tone: 'neutral' },
};

export const POLICY: Record<string, string> = {
  AUTO: 'Automatic',
  APPROVAL: 'Needs approval',
  FORBIDDEN: 'Not allowed',
  REQUIRE_APPROVAL: 'Send to approval',
  REJECT: 'Reject',
  EXCLUDE: 'Exclude',
};

export const plural = (n: number, one: string, many = one + 's') => `${n} ${n === 1 ? one : many}`;
