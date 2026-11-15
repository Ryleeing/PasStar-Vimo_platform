// Step 0: the protocol the workspace's agent acts under now. One fetch shared by the onboarding gate, the
// sidebar progress and the authorisation strip, so they always agree on whether step 0 is done.
import { createContext, useContext, useRef, type ReactNode } from 'react';
import { getActiveProtocol } from '../api/endpoints';
import { ApiError } from '../api/errors';
import type { Protocol } from '../api/types';
import { useAsync } from '../components/ui';
import { useSession } from './session';

export interface ProtocolState {
  /** 'none': this buyer has not signed a protocol yet (or Valio has not confirmed it), so step 0 is still open. */
  status: 'loading' | 'active' | 'none' | 'error' | 'n/a';
  protocol: Protocol | null;
  error?: unknown;
  reload: () => void;
}

const Ctx = createContext<ProtocolState>({ status: 'n/a', protocol: null, reload: () => undefined });

export function ProtocolProvider({ children }: { children: ReactNode }) {
  const { account, workspace } = useSession();
  const applies = workspace.kind === 'buyer' && !!workspace.agentId;
  const p = useAsync(
    () => (applies ? getActiveProtocol(workspace.agentId!) : Promise.resolve(null)),
    [workspace.agentId, account.key],
  );
  const none = p.error instanceof ApiError && (p.error.code === 'PROTOCOL_INVALID' || p.error.code === 'NOT_FOUND');
  // A re-check keeps the last answer on screen (no loading flash, no remounting the page) until the new one is in.
  const last = useRef<ProtocolState['status'] | null>(null);
  const settled: ProtocolState['status'] = !applies ? 'n/a' : none ? 'none' : p.error ? 'error' : p.data ? 'active' : 'loading';
  const status = p.loading && last.current && last.current !== 'loading' ? last.current : p.loading && !p.data ? 'loading' : settled;
  if (!p.loading) last.current = settled;
  const value: ProtocolState = {
    status,
    protocol: p.data ?? null,
    error: p.error,
    reload: () => void p.reload(),
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useActiveProtocol = () => useContext(Ctx);
