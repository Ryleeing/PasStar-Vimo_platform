// Demo accounts. Switching account = switching the X-Demo-User header (no real login, per the design doc).
// Each buyer workspace carries its approval mode, so "mode" and "role" are picked in one place.
import type { LucideIcon } from 'lucide-react';
import {
  Activity, ClipboardCheck, FileSignature, Inbox, LayoutGrid, MessageSquarePlus, Package, ServerCog, SlidersHorizontal,
} from 'lucide-react';

export type RoleKey = 'chef' | 'buyer_manager' | 'owner' | 'valio_reviewer';
export type PageKey = 'request' | 'options' | 'review' | 'approve' | 'ordered' | 'orders' | 'approvals' | 'protocol' | 'profile' | 'audit' | 'valio' | 'agents';
export type WorkspaceKey = 'hotel' | 'bistro' | 'valio';

export interface Account {
  key: RoleKey;
  userId: string; // X-Demo-User
  name: string;
  title: string;
  workspace: WorkspaceKey;
}

export interface Workspace {
  key: WorkspaceKey;
  name: string;
  kind: 'buyer' | 'seller';
  mode?: 'SEPARATE' | 'SELF';
  modeLabel: string;
  buyerId?: string;
  /** The agent named in this workspace's protocol (backend seed: app/channel/seed.py). */
  agentId?: string;
}

export const WORKSPACES: Record<WorkspaceKey, Workspace> = {
  hotel: { key: 'hotel', name: 'Hotel Helsinki Central', kind: 'buyer', mode: 'SEPARATE', modeLabel: 'Organisation · separate approver', buyerId: 'buy_hotel', agentId: 'agt_hotel' },
  bistro: { key: 'bistro', name: 'Bistro Kallio', kind: 'buyer', mode: 'SELF', modeLabel: 'Single owner · self-approval logged', buyerId: 'buy_bistro', agentId: 'agt_bistro' },
  valio: { key: 'valio', name: 'Valio Aimo', kind: 'seller', modeLabel: 'Channel owner' },
};

export const ACCOUNTS: Account[] = [
  { key: 'chef', userId: 'usr_chef', name: 'Aino', title: 'Chef', workspace: 'hotel' },
  { key: 'buyer_manager', userId: 'usr_manager', name: 'Mikko', title: 'Procurement manager', workspace: 'hotel' },
  { key: 'owner', userId: 'usr_owner', name: 'Sanna', title: 'Owner', workspace: 'bistro' },
  { key: 'valio_reviewer', userId: 'usr_valio', name: 'Elina', title: 'Valio reviewer', workspace: 'valio' },
];

export interface PageDef {
  key: PageKey;
  label: string;
  icon: LucideIcon;
  path: string;
  /** Reachable by navigation only (e.g. the order confirmation), not listed in the sidebar. */
  hidden?: boolean;
}

const P: Record<PageKey, PageDef> = {
  request: { key: 'request', label: 'New request', icon: MessageSquarePlus, path: '/request' },
  options: { key: 'options', label: 'Options', icon: LayoutGrid, path: '/options' },
  review: { key: 'review', label: 'Review order', icon: Package, path: '/review', hidden: true },
  approve: { key: 'approve', label: 'Review and approve', icon: ClipboardCheck, path: '/approve', hidden: true },
  ordered: { key: 'ordered', label: 'Order placed', icon: Package, path: '/ordered', hidden: true },
  orders: { key: 'orders', label: 'My orders', icon: Package, path: '/orders' },
  approvals: { key: 'approvals', label: 'Approvals', icon: ClipboardCheck, path: '/approvals' },
  protocol: { key: 'protocol', label: 'Authorization protocol', icon: FileSignature, path: '/protocol' },
  profile: { key: 'profile', label: 'Buying profile', icon: SlidersHorizontal, path: '/profile' },
  audit: { key: 'audit', label: 'Activity log', icon: Activity, path: '/audit' },
  valio: { key: 'valio', label: 'Review queue', icon: Inbox, path: '/valio' },
  agents: { key: 'agents', label: 'Agents', icon: ServerCog, path: '/agents' },
};

export const PAGES_BY_ROLE: Record<RoleKey, PageDef[]> = {
  chef: [P.request, P.options, P.orders, P.profile, P.agents, P.audit, P.review, P.ordered],
  buyer_manager: [P.approvals, P.protocol, P.agents, P.audit, P.approve, P.ordered],
  owner: [P.request, P.options, { ...P.approvals, label: 'Needs your approval' }, P.orders, P.protocol, P.agents, P.profile, P.audit, P.review, P.approve, P.ordered],
  valio_reviewer: [P.valio, { ...P.agents, label: 'Agents & faults' }, { ...P.audit, label: 'Full audit log' }],
};

/** The accounts offered in the switcher. The hotel's two-person mode (chef + manager) is kept in the backend and the
 *  code but not shown: the demo tells one story, the one-person bistro, plus Valio. */
export const SHOWN_ROLES: RoleKey[] = ['owner', 'valio_reviewer'];

export const accountByKey = (k: RoleKey) => ACCOUNTS.find((a) => a.key === k)!;
export const accountByUserId = (u?: string | null) => ACCOUNTS.find((a) => a.userId === u);
export const displayUser = (u?: string | null) => {
  const a = accountByUserId(u);
  return a ? `${a.name} (${a.title})` : (u ?? '—');
};
