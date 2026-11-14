import { useCallback, useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { Check, ChevronsUpDown, Moon, RotateCcw, ShieldCheck, Sun } from 'lucide-react';
import { ACCOUNTS, PAGES_BY_ROLE, WORKSPACES, type RoleKey } from '../config/accounts';
import { useSession } from '../state/session';
import { progressSteps, useFlow } from '../state/flow';
import { listApprovals, resetDemo } from '../api/endpoints';
import { API_BASE, type ApiMode } from '../api/client';
import { Avatar, Button, useAsync, useDismiss, useToast } from './ui';

export function Sidebar({ open, onNavigate }: { open: boolean; onNavigate: () => void }) {
  const { account, workspace, mode } = useSession();
  const { flow } = useFlow();
  const pages = PAGES_BY_ROLE[account.key];

  const queueKind = account.key === 'valio_reviewer' ? 'VALIO' : account.key === 'chef' ? null : 'BUYER';
  const pending = useAsync(
    () => (queueKind ? listApprovals(queueKind, 'PENDING') : Promise.resolve([])),
    [account.key, flow.order?.status, flow.approvalResult],
  );
  const pendingCount = (pending.data ?? []).filter((a) => !flow.decisions[a.id]).length;

  return (
    <aside className={`sidebar ${open ? 'open' : ''}`} aria-label="Workspace navigation">
      <div className="brand">
        <div className="brand-mark"><ShieldCheck size={18} /></div>
        <div>
          <b>The Pass</b>
          <span>Valio Aimo open procurement channel</span>
        </div>
      </div>

      <AccountSwitcher />

      <nav className="nav" aria-label="Pages">
        <div className="side-label" title={workspace.modeLabel}>{workspace.kind === 'buyer' ? (workspace.mode === 'SELF' ? 'Single-owner mode' : 'Organisation mode') : 'Channel owner'}</div>
        {pages.map((p) => (
          <NavLink key={p.key} to={p.path} onClick={onNavigate} className={({ isActive }) => (isActive ? 'active' : '')}>
            <p.icon size={17} aria-hidden />
            {p.label}
            {(p.key === 'approvals' || p.key === 'valio') && pendingCount > 0 && <span className="count" aria-label={`${pendingCount} pending`}>{pendingCount}</span>}
          </NavLink>
        ))}
      </nav>

      {workspace.kind === 'buyer' ? <ProgressTracker mode={mode} onNavigate={onNavigate} /> : <ValioSummary pending={pendingCount} />}

      <SideFooter />
    </aside>
  );
}

function AccountSwitcher() {
  const { account, workspace, switchAccount } = useSession();
  const { reset } = useFlow();
  const navigate = useNavigate();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  const ref = useDismiss(open, close);

  const pick = (k: RoleKey) => {
    const next = ACCOUNTS.find((a) => a.key === k)!;
    setOpen(false);
    if (k === account.key) return;
    // A different buyer organisation starts a fresh journey; same organisation keeps it (chef → manager hand-off).
    if (next.workspace !== account.workspace && next.workspace !== 'valio' && account.workspace !== 'valio') reset();
    switchAccount(k);
    navigate(PAGES_BY_ROLE[k][0].path);
    toast(`Signed in as ${next.name} · ${next.title}`, 'info');
  };

  return (
    <div className="switcher" ref={ref}>
      <button className="account-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <Avatar role={account.key} name={account.name} />
        <span className="who">
          <b>{account.name}</b>
          <span>{account.title} · {workspace.name}</span>
        </span>
        <ChevronsUpDown size={16} className="muted" aria-hidden />
      </button>
      {open && (
        <div className="switcher-menu" role="menu" aria-label="Switch account">
          {Object.values(WORKSPACES).map((ws) => (
            <div key={ws.key} className="switcher-group">
              <div className="ws"><b>{ws.name}</b><span>{ws.mode ?? 'Seller'}</span></div>
              {ACCOUNTS.filter((a) => a.workspace === ws.key).map((a) => (
                <button key={a.key} className="switcher-item" role="menuitemradio" aria-checked={a.key === account.key} onClick={() => pick(a.key)}>
                  <Avatar role={a.key} name={a.name} size="sm" />
                  <span className="grow">
                    <span className="strong">{a.name}</span> <span className="sub">· {a.title}</span>
                    <span className="sub mono" style={{ display: 'block' }}>{a.userId}</span>
                  </span>
                  {a.key === account.key && <Check size={16} color="var(--accent)" aria-hidden />}
                </button>
              ))}
            </div>
          ))}
          <div className="menu-note">Demo accounts. Switching sets the <span className="mono">X-Demo-User</span> header.</div>
        </div>
      )}
    </div>
  );
}

function ProgressTracker({ mode, onNavigate }: { mode: 'SEPARATE' | 'SELF'; onNavigate: () => void }) {
  const { flow } = useFlow();
  const { account } = useSession();
  const navigate = useNavigate();
  const steps = progressSteps(flow, mode);
  const allowed = new Set(PAGES_BY_ROLE[account.key].map((p) => p.key));
  const go = (page?: string) => {
    if (!page) return;
    const target = allowed.has(page as never) ? page : page === 'orders' && allowed.has('approvals') ? 'approvals' : page === 'approvals' && allowed.has('orders') ? 'orders' : null;
    if (target) {
      navigate('/' + target);
      onNavigate();
    }
  };
  const doneCount = steps.filter((s) => s.state === 'done' || s.state === 'skipped').length;
  return (
    <section className="progress" aria-label="Order progress">
      <div className="progress-head">
        <span className="side-label" style={{ padding: 0, margin: 0 }}>{flow.request ? `Request ${flow.request.request_id}` : 'Current order'}</span>
        <span className="xs muted">{doneCount}/{steps.length}</span>
      </div>
      <ol className="steps">
        {steps.map((s, i) => (
          <li key={s.key} className={`step ${s.state}`} aria-current={s.state === 'current' ? 'step' : undefined}>
            <span className="step-dot">{s.state === 'done' ? <Check size={12} strokeWidth={3} /> : s.state === 'blocked' ? '!' : s.state === 'skipped' ? '–' : i + 1}</span>
            <div className="step-body">
              <button onClick={() => go(s.page)}>{s.label}</button>
              <div className="hint" title={s.hint}>{s.hint}</div>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function ValioSummary({ pending }: { pending: number }) {
  return (
    <section className="progress" aria-label="Channel status">
      <div className="side-label" style={{ padding: 0 }}>Channel today</div>
      <div className="stack sm" style={{ marginTop: 8 }}>
        <div className="row between small"><span className="muted">Review queue</span><b>{pending} pending</b></div>
        <div className="row between small"><span className="muted">Connected agents</span><b>3</b></div>
        <div className="row between small"><span className="muted">Probe alerts (24 h)</span><b style={{ color: 'var(--bad)' }}>3</b></div>
      </div>
    </section>
  );
}

function SideFooter() {
  const { theme, toggleTheme, apiMode, setApiMode, source } = useSession();
  const { reset } = useFlow();
  const toast = useToast();
  const label = source === 'live' ? 'Live API' : source === 'mock' ? 'Mock data' : 'Connecting…';
  return (
    <div className="side-foot">
      <div className="source" title={source === 'live' ? API_BASE : 'In-browser mock built from fixtures/*.json'}>
        <span className={`dot ${source}`} aria-hidden />
        <span>{label}</span>
        <label className="sr-only" htmlFor="api-mode">Data source</label>
        <select id="api-mode" value={apiMode} onChange={(e) => setApiMode(e.target.value as ApiMode)}>
          <option value="auto">Auto</option>
          <option value="live">Live</option>
          <option value="mock">Mock</option>
        </select>
      </div>
      <div className="row">
        <Button variant="ghost" size="sm" onClick={toggleTheme} aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}>
          {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
          {theme === 'dark' ? 'Light' : 'Dark'}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="ml-auto"
          onClick={async () => {
            await resetDemo().catch(() => undefined);
            reset();
            toast('Demo data reset', 'info');
          }}
        >
          <RotateCcw size={15} /> Reset demo
        </Button>
      </div>
    </div>
  );
}
