// On a public deployment the screens first ask for the demo passcode; locally this renders its children at once.
// The passcode is exchanged for a session token (backend/app/demo_access.py) sent with every request afterwards.
import { useEffect, useState, type ReactNode } from 'react';
import { LockKeyhole } from 'lucide-react';
import { API_BASE, apiState } from '../api/client';
import { Button } from './ui';

type Status = 'checking' | 'open' | 'locked';

async function check(): Promise<Status> {
  try {
    const r = await fetch(`${API_BASE}/v1/session`, { headers: apiState.session ? { 'X-Demo-Session': apiState.session } : {} });
    const s = await r.json();
    return !s.public || s.signed_in ? 'open' : 'locked';
  } catch {
    return 'open';            // no backend: the in-browser mock takes over, nothing to unlock
  }
}

export function PasscodeGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>('checking');
  const [code, setCode] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { void check().then(setStatus); }, []);

  const submit = async () => {
    if (!code.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await fetch(`${API_BASE}/v1/session`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ passcode: code.trim() }),
      });
      if (!r.ok) throw new Error('wrong');
      apiState.setSession((await r.json()).token);
      setStatus('open');
    } catch {
      setError('That passcode is not right.');
    } finally {
      setBusy(false);
    }
  };

  if (status === 'open') return <>{children}</>;
  if (status === 'checking') return <div className="gate" aria-busy="true" />;
  return (
    <main className="gate">
      <form className="gate-card card" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
        <img src="/passtar-logo.png" alt="PasStar" width={96} height={96} />
        <h1>PasStar demo</h1>
        <p className="muted">Valio Aimo’s ordering channel for AI purchasing agents. Enter the demo passcode to continue.</p>
        <label className="sr-only" htmlFor="passcode">Demo passcode</label>
        <input id="passcode" type="password" autoComplete="current-password" value={code} onChange={(e) => setCode(e.target.value)}
          placeholder="Demo passcode" autoFocus />
        {error && <p className="bad-text small" role="alert">{error}</p>}
        <Button variant="primary" type="submit" loading={busy} disabled={!code.trim()}><LockKeyhole size={16} /> Enter</Button>
      </form>
    </main>
  );
}
