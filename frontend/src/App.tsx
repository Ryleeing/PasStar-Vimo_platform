import { useState, type ReactNode } from 'react';
import { HashRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { Menu, ShieldCheck } from 'lucide-react';
import { SessionProvider, useSession } from './state/session';
import { FlowProvider } from './state/flow';
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

function Shell() {
  const { account } = useSession();
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();
  const home = PAGES_BY_ROLE[account.key][0].path;
  return (
    <div className="shell">
      <div className="mobile-bar">
        <button className="btn ghost icon-btn" aria-label="Open navigation" onClick={() => setMenuOpen(true)}><Menu size={18} /></button>
        <ShieldCheck size={18} color="var(--accent)" aria-hidden />
        <b>The Pass</b>
      </div>
      {menuOpen && <div className="scrim" onClick={() => setMenuOpen(false)} aria-hidden />}
      <Sidebar open={menuOpen} onNavigate={() => setMenuOpen(false)} />
      <main className="content" id="main">
        <div className="content-inner fade-in" key={location.pathname + account.key}>
          <Routes>
            {(Object.keys(ROUTES) as PageKey[]).map((k) => {
              const Page = ROUTES[k];
              return <Route key={k} path={'/' + k} element={<Guard page={k}><Page /></Guard>} />;
            })}
            <Route path="*" element={<Navigate to={home} replace />} />
          </Routes>
        </div>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <HashRouter>
      <SessionProvider>
        <ToastProvider>
          <FlowProvider>
            <Shell />
          </FlowProvider>
        </ToastProvider>
      </SessionProvider>
    </HashRouter>
  );
}
