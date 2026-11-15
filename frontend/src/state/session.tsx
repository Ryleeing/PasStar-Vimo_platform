import { createContext, useCallback, useContext, useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from 'react';
import { apiState, type ApiMode, type DataSource } from '../api/client';
import { accountByKey, WORKSPACES, type Account, type RoleKey, type Workspace } from '../config/accounts';

type Theme = 'dark' | 'light';

interface Session {
  account: Account;
  workspace: Workspace;
  mode: 'SEPARATE' | 'SELF';
  switchAccount: (k: RoleKey) => void;
  theme: Theme;
  toggleTheme: () => void;
  apiMode: ApiMode;
  setApiMode: (m: ApiMode) => void;
  source: DataSource;
}

const Ctx = createContext<Session | null>(null);

const load = <T extends string>(key: string, ok: readonly T[], fallback: T): T => {
  try {
    const v = localStorage.getItem(key) as T | null;
    return v && ok.includes(v) ? v : fallback;
  } catch {
    return fallback;
  }
};
const save = (key: string, v: string) => {
  try {
    localStorage.setItem(key, v);
  } catch {
    /* ignore */
  }
};

export function SessionProvider({ children }: { children: ReactNode }) {
  const [role, setRole] = useState<RoleKey>(() => load('thepass.role', ['owner', 'valio_reviewer'] as const, 'owner'));
  const [theme, setTheme] = useState<Theme>(() => load('thepass.theme', ['dark', 'light'] as const, 'light'));
  const account = accountByKey(role);
  const workspace = WORKSPACES[account.workspace];

  // Set synchronously so the very first request already carries the right X-Demo-User.
  apiState.setDemoUser(account.userId);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    save('thepass.theme', theme);
  }, [theme]);

  const switchAccount = useCallback((k: RoleKey) => {
    setRole(k);
    save('thepass.role', k);
  }, []);

  const source = useSyncExternalStore(apiState.subscribe, () => apiState.source);
  const apiMode = useSyncExternalStore(apiState.subscribe, () => apiState.mode);

  const value = useMemo<Session>(
    () => ({
      account,
      workspace,
      mode: workspace.mode ?? 'SEPARATE',
      switchAccount,
      theme,
      toggleTheme: () => setTheme((t) => (t === 'dark' ? 'light' : 'dark')),
      apiMode,
      setApiMode: apiState.setMode,
      source,
    }),
    [account, workspace, switchAccount, theme, apiMode, source],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession() {
  const s = useContext(Ctx);
  if (!s) throw new Error('useSession outside SessionProvider');
  return s;
}
