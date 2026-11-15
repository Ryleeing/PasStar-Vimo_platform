import { useState, type ReactNode } from 'react';
import { HashRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { Menu } from 'lucide-react';
import { SessionProvider, useSession } from './state/session';
import { FlowProvider } from './state/flow';
import { ProtocolProvider, useActiveProtocol } from './state/protocol';
import { PAGES_BY_ROLE, type PageKey } from './config/accounts';
import { Sidebar } from './components/Sidebar';
import { ToastProvider } from './components/ui';
import { RequestPage } from './pages/RequestPage';
import { OptionsPage } from './pages/OptionsPage';
import { OrdersPage } from './pages/OrdersPage';
import { OrderedPage } from './pages/OrderedPage';
import { ReviewPage } from './pages/ReviewPage';
import { ApproveReviewPage } from './pages/ApproveReviewPage';
import { ApprovalsPage } from './pages/ApprovalsPage';
import { ProtocolPage } from './pages/ProtocolPage';
import { ProfilePage } from './pages/ProfilePage';
import { AuditPage } from './pages/AuditPage';
import { ValioQueuePage } from './pages/ValioQueuePage';
import { AgentsPage } from './pages/AgentsPage';
import { OnboardingPage } from './pages/OnboardingPage';
import { PasscodeGate } from './components/PasscodeGate';
import { ErrorState, Skeleton } from './components/ui';

const ROUTES: Record<PageKey, () => JSX.Element> = {
  request: RequestPage,
  options: OptionsPage,
  review: ReviewPage,
  approve: ApproveReviewPage,
  ordered: OrderedPage,
  orders: OrdersPage,
  approvals: ApprovalsPage,
  protocol: ProtocolPage,
  profile: ProfilePage,
  audit: AuditPage,
  valio: ValioQueuePage,
  agents: AgentsPage,
};

/** Each account only sees its own pages; anything else redirects to the account's home page. */
function Guard({ page, children }: { page: PageKey; children: ReactNode }) {
  const { account } = useSession();
  const pages = PAGES_BY_ROLE[account.key];
  return pages.some((p) => p.key === page) ? <>{children}</> : <Navigate to={pages[0].path} replace />;
}

/** Step 0 first: a buyer workspace without an active protocol sees only the authorisation page. */
function ProtocolGate({ children }: { children: ReactNode }) {
  const p = useActiveProtocol();
  if (p.status === 'loading') return <Skeleton h={320} />;
  if (p.status === 'none') return <OnboardingPage />;
  if (p.status === 'error') return <ErrorState error={p.error} onRetry={p.reload} />;
  return <>{children}</>;
}

function Shell() {
  const { account } = useSession();
  const [menuOpen, setMenuOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(() => { try { return localStorage.getItem('thepass.sidebar') === 'collapsed'; } catch { return false; } });
  const toggleCollapsed = () => setCollapsed((c) => {
    try { localStorage.setItem('thepass.sidebar', c ? 'open' : 'collapsed'); } catch { /* private mode */ }
    return !c;
  });
  const location = useLocation();
  const home = PAGES_BY_ROLE[account.key][0].path;
  return (
    <div className={`shell ${collapsed ? 'collapsed' : ''}`}>
      <div className="mobile-bar">
        <button className="btn ghost icon-btn" aria-label="Open navigation" onClick={() => setMenuOpen(true)}><Menu size={18} /></button>
        <img className="brand-logo sm" src="/passtar-logo.png" alt="" width={36} height={36} />
        <b>PasStar</b>
      </div>
      {menuOpen && <div className="scrim" onClick={() => setMenuOpen(false)} aria-hidden />}
      <Sidebar open={menuOpen} collapsed={collapsed} onToggle={toggleCollapsed} onNavigate={() => setMenuOpen(false)} />
      <main className="content" id="main">
        <div className="content-inner" key={location.pathname + account.key}>
          <ProtocolGate>
          <Routes>
            {(Object.keys(ROUTES) as PageKey[]).map((k) => {
              const Page = ROUTES[k];
              return <Route key={k} path={'/' + k} element={<Guard page={k}><Page /></Guard>} />;
            })}
            <Route path="*" element={<Navigate to={home} replace />} />
          </Routes>
          </ProtocolGate>
        </div>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <HashRouter>
      <PasscodeGate>
      <SessionProvider>
        <ToastProvider>
          <FlowProvider>
            <ProtocolProvider>
              <Shell />
            </ProtocolProvider>
          </FlowProvider>
        </ToastProvider>
      </SessionProvider>
      </PasscodeGate>
    </HashRouter>
  );
}
