// Agents: who may act for this buyer, how each one connects, and its key ("work badge").
//   Connected       every external agent, its kind and connection, the protocol it acts under, its key and activity.
//   Connect an agent  pick the kind → pick the protocol → PasStar issues a key (shown once) → setup → test.
// Valio sees every buyer's agents and the demo fault switches; a buyer sees its own, and only the approver named
// in the protocol may issue or revoke keys.
import { useEffect, useState, type ReactNode } from 'react';
import { Bot, Building2, Check, Copy, KeyRound, Link2, PlugZap, ShieldAlert, ShieldCheck, Sparkles, Trash2 } from 'lucide-react';
import { issueAgentKey, listAgents, listFaults, protocolAsAgent, revokeAgentKey, setFault } from '../api/endpoints';
import { describeError } from '../api/client';
import type { AgentConnection, AgentKind, Fault, IssuedKey, Protocol } from '../api/types';
import { accountByUserId } from '../config/accounts';
import { eur } from '../lib/format';
import { useSession } from '../state/session';
import { Banner, Button, Empty, ErrorState, PageHead, Pill, Segmented, Skeleton, Switch, useAsync, useToast } from '../components/ui';

const KIND: Record<AgentKind, { label: string; icon: ReactNode; how: string }> = {
  CHATGPT: { label: 'ChatGPT', icon: <Sparkles size={18} />, how: 'PasStar plugin for ChatGPT: MCP over HTTPS, key in the X-Agent-Key header' },
  CLAUDE: { label: 'Claude', icon: <Bot size={18} />, how: 'PasStar connector for Claude: MCP over HTTPS, key in the X-Agent-Key header' },
  ERP: { label: 'Your ERP', icon: <Building2 size={18} />, how: 'Your purchasing system calls the REST API with the key; over-limit orders come back signed by your own approval flow' },
  UNKNOWN: { label: 'Unknown', icon: <ShieldAlert size={18} />, how: 'Not connected through any approved route' },
};
const CONNECTABLE: AgentKind[] = ['CHATGPT', 'CLAUDE', 'ERP'];
const FAULT_TEXT: Record<Fault['name'], string> = {
  PRICE_CHANGE: 'Price changes between quote and re-check',
  PARTIAL_STOCK: 'Only part of the quantity is deliverable',
  TIMEOUT: 'Valio API times out',
  RATE_LIMIT: 'Valio API returns 429',
  EMPTY_RESULT: 'Search returns nothing',
};
const PUBLIC_URL_KEY = 'passtar.publicUrl';

const ago = (iso?: string | null) => {
  if (!iso) return 'never';
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
};
const person = (userId?: string | null) => {
  const a = accountByUserId(userId);
  return a ? `${a.name}, ${a.title.toLowerCase()}` : userId ?? 'nobody';
};

export function AgentsPage() {
  const { account, workspace } = useSession();
  const valio = workspace.kind === 'seller';
  const list = useAsync(listAgents, [account.key]);
  const [tab, setTab] = useState<'connected' | 'connect'>('connected');
  const [preset, setPreset] = useState<string | null>(null);
  const canManage = (a: AgentConnection) => a.approver === account.userId;

  const connectAgain = (a: AgentConnection) => {
    setPreset(a.agent_id);
    setTab('connect');
  };

  return (
    <>
      <PageHead title={valio ? 'Agents & faults' : 'Agents'}
        desc={valio
          ? 'Every buyer’s external agents: how they connect, the protocol they act under, and what they did in the last 24 hours.'
          : 'The AI assistants and systems that may order for you. Each one gets its own key, tied to your signed protocol.'} />

      {!valio && (
        <div style={{ marginBottom: 16 }}>
          <Segmented label="View" value={tab} onChange={(v) => { setTab(v); setPreset(null); }}
            options={[['connected', 'Connected'], ['connect', 'Connect an agent']]} />
        </div>
      )}

      {tab === 'connect' && !valio ? (
        <ConnectAgent agents={list.data ?? []} preset={preset} onDone={() => { list.reload(); }} />
      ) : list.loading ? <Skeleton h={320} /> : list.error ? <ErrorState error={list.error} onRetry={list.reload} /> : (
        <div className={valio ? 'grid two' : ''} style={{ alignItems: 'start' }}>
          <div className="agent-grid">
            {(list.data ?? []).map((a) => (
              <AgentCard key={a.agent_id} a={a} showBuyer={valio} canManage={canManage(a)}
                onConnect={() => connectAgain(a)} onChanged={list.reload} />
            ))}
            {!list.data?.length && <Empty icon={<Bot size={22} />} title="No agents yet">Connect one to let it order for you.</Empty>}
          </div>
          {valio && <Faults />}
        </div>
      )}
    </>
  );
}

function AgentCard({ a, showBuyer, canManage, onConnect, onChanged }: {
  a: AgentConnection; showBuyer: boolean; canManage: boolean; onConnect: () => void; onChanged: () => void;
}) {
  const toast = useToast();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const k = KIND[a.kind];
  const status = !a.protocol_id
    ? { tone: 'bad' as const, label: 'No protocol · every call refused' }
    : !a.has_key ? { tone: 'warn' as const, label: 'No key' } : { tone: 'ok' as const, label: 'Connected' };

  const revoke = async () => {
    setBusy(true);
    try {
      await revokeAgentKey(a.agent_id);
      toast(`${a.name}: key revoked`, 'info');
      onChanged();
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };

  return (
    <section className={`card agent-card ${status.tone}`} aria-label={a.name}>
      <header className="agent-head">
        <span className={`agent-kind k-${a.kind.toLowerCase()}`} aria-hidden>{k.icon}</span>
        <div className="grow">
          <h3>{a.name}</h3>
          <div className="small muted">{k.label}{showBuyer && <> · {a.buyer_name}</>}</div>
        </div>
        <Pill tone={status.tone}>{status.label}</Pill>
      </header>

      <dl className="agent-facts">
        <div><dt><Link2 size={14} aria-hidden /> Connects</dt><dd>{a.protocol_id ? k.how : 'Not allowed: no signed protocol names this agent'}</dd></div>
        <div><dt><ShieldCheck size={14} aria-hidden /> Acts under</dt><dd>{a.protocol_id ? <>Protocol {a.protocol_id} v{a.protocol_version} · key managed by {person(a.approver)}</> : '—'}</dd></div>
        <div><dt><KeyRound size={14} aria-hidden /> Key</dt><dd>{a.has_key ? <><span className="mono">{a.key_hint}…</span> · issued {ago(a.key_issued_at)} · last used {ago(a.last_used_at)}</> : 'none'}</dd></div>
        <div><dt><PlugZap size={14} aria-hidden /> Last 24 h</dt><dd>
          {a.activity.requests_24h} call{a.activity.requests_24h === 1 ? '' : 's'}
          {a.activity.blocked_24h > 0 && <> · <span className="bad-text">{a.activity.blocked_24h} blocked</span></>}
          {a.activity.probe_alerts_24h > 0 && <> · <span className="bad-text">{a.activity.probe_alerts_24h} stock-probing alerts</span></>}
        </dd></div>
      </dl>

      {canManage && a.protocol_id && (
        <div className="row wrap" style={{ gap: 8 }}>
          <Button size="sm" onClick={onConnect}><KeyRound size={15} /> {a.has_key ? 'Issue a new key' : 'Connect'}</Button>
          {a.has_key && (confirming ? (
            <>
              <Button size="sm" variant="outline-danger" loading={busy} onClick={revoke}>Revoke now</Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>Keep it</Button>
            </>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => setConfirming(true)}><Trash2 size={15} /> Revoke</Button>
          ))}
        </div>
      )}
      {confirming && <p className="small muted">The agent stops working immediately; every later call is refused and reported.</p>}
    </section>
  );
}

/** Connect an agent: kind → protocol → key (shown once) → setup → test. */
function ConnectAgent({ agents, preset, onDone }: { agents: AgentConnection[]; preset: string | null; onDone: () => void }) {
  const { account } = useSession();
  const toast = useToast();
  const mine = agents.filter((a) => a.protocol_id && a.approver === account.userId);
  const [kind, setKind] = useState<AgentKind | null>(() => (preset ? agents.find((a) => a.agent_id === preset)?.kind ?? null : null));
  const [agentId, setAgentId] = useState<string | null>(preset ?? (mine.length === 1 ? mine[0].agent_id : null));
  const [issued, setIssued] = useState<IssuedKey | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [url, setUrl] = useState(() => { try { return localStorage.getItem(PUBLIC_URL_KEY) ?? ''; } catch { return ''; } });
  const [test, setTest] = useState<{ ok: true; p: Protocol } | { ok: false; error: string } | null>(null);
  const [testing, setTesting] = useState(false);

  useEffect(() => { try { localStorage.setItem(PUBLIC_URL_KEY, url); } catch { /* private mode */ } }, [url]);

  if (!mine.length) {
    return (
      <Banner tone="info" title="Sign your protocol first">
        An agent gets a key only under an active protocol, signed by you and confirmed by Valio (step 0).
        {agents.length > 0 && <> Only the approver named in the protocol can connect agents.</>}
      </Banner>
    );
  }

  const issue = async () => {
    if (!agentId || !kind) return;
    setBusy(true);
    try {
      setIssued(await issueAgentKey(agentId, kind));
      setTest(null);
      onDone();
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setBusy(false);
    }
  };
  const copy = async (text: string, what: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(what);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      toast('Copy failed; select the text instead', 'bad');
    }
  };
  const runTest = async () => {
    if (!issued) return;
    setTesting(true);
    try {
      setTest({ ok: true, p: await protocolAsAgent(issued.agent_id, issued.key) });
    } catch (e) {
      setTest({ ok: false, error: describeError(e) });
    } finally {
      setTesting(false);
    }
  };

  const host = url.trim().replace(/\/+$/, '') || 'https://<your PasStar address>';
  const setup: Step[] = issued && kind && kind !== 'UNKNOWN' ? SETUP[kind](host, issued) : [];
  const stage = issued ? 2 : kind && agentId ? 1 : 0;

  return (
    <div className="stack lg connect">
      <ol className="onboard-stages" aria-label="Connect an agent">
        {['Choose the agent', 'Issue its key', 'Set it up and test'].map((s, i) => (
          <li key={s} className={i < stage ? 'done' : i === stage ? 'current' : 'todo'}>
            <span className="step-dot">{i < stage ? <Check size={14} strokeWidth={3} /> : i + 1}</span> {s}
          </li>
        ))}
      </ol>

      <section className="card stack">
        <h2>Which agent?</h2>
        <div className="kind-pick">
          {CONNECTABLE.map((k) => (
            <button key={k} type="button" className={`kind-option ${kind === k ? 'on' : ''}`} aria-pressed={kind === k}
              disabled={!!issued} onClick={() => setKind(k)}>
              <span className={`agent-kind k-${k.toLowerCase()}`} aria-hidden>{KIND[k].icon}</span>
              <b>{KIND[k].label}</b>
              <span className="small muted">{KIND[k].how}</span>
            </button>
          ))}
        </div>
        <div className="field">
          <span className="label">Acts under</span>
          <div className="chips">
            {mine.map((a) => (
              <button key={a.agent_id} type="button" className="chip" aria-pressed={agentId === a.agent_id} disabled={!!issued}
                onClick={() => setAgentId(a.agent_id)}>
                {agentId === a.agent_id && <Check size={14} />}{a.name} · {a.protocol_id} v{a.protocol_version}
              </button>
            ))}
          </div>
          <span className="small muted">One agent per protocol. Issuing a key replaces any key this agent had before.</span>
        </div>
        {!issued && (
          <div className="row">
            <Button variant="primary" onClick={issue} loading={busy} disabled={!kind || !agentId}><KeyRound size={15} /> Issue the key</Button>
          </div>
        )}
      </section>

      {issued && (
        <section className="card stack" aria-labelledby="key-title">
          <h2 id="key-title">The key for {KIND[issued.kind].label}</h2>
          <div className="key-box">
            <code className="mono">{issued.key}</code>
            <Button size="sm" onClick={() => copy(issued.key, 'key')}>{copied === 'key' ? <><Check size={15} /> Copied</> : <><Copy size={15} /> Copy</>}</Button>
          </div>
          <Banner tone="warn" title="Shown only now">
            PasStar keeps only a fingerprint of it. Give it to the agent, never in a chat or an email. Lost it? Issue a new one.
          </Banner>

          <div className="field">
            <label htmlFor="public-url">Your PasStar address (the tunnel or server the agent can reach)</label>
            <input id="public-url" value={url} placeholder="https://xxxx.trycloudflare.com" onChange={(e) => setUrl(e.target.value)} />
          </div>
          <ol className="setup-steps">
            {setup.map((s, i) => (
              <li key={i}>
                <p>{s.text}</p>
                {s.code && (
                  <div className="key-box">
                    <code className="mono">{s.code}</code>
                    <Button size="sm" variant="ghost" onClick={() => copy(s.code!, `s${i}`)}>{copied === `s${i}` ? <Check size={15} /> : <Copy size={15} />}</Button>
                  </div>
                )}
              </li>
            ))}
          </ol>

          <div className="row wrap" style={{ gap: 8 }}>
            <Button onClick={runTest} loading={testing}><PlugZap size={15} /> Test the connection</Button>
            <Button variant="ghost" onClick={() => { setIssued(null); setKind(null); setTest(null); }}>Connect another</Button>
          </div>
          {test?.ok && (
            <Banner tone="ok" title="Connected">
              With this key the agent acts for {agents.find((a) => a.agent_id === issued.agent_id)?.buyer_name ?? test.p.body.buyer_id} under {test.p.id} v{test.p.version}: it may order up to
              {' '}{eur(test.p.body.auto_approve_limit_cents)} on its own, never above {eur(test.p.body.max_order_cents)}.
            </Banner>
          )}
          {test && !test.ok && <Banner tone="bad" title="Not connected">{test.error}</Banner>}
        </section>
      )}
    </div>
  );
}

type Step = { text: string; code?: string };
const SETUP: Record<Exclude<AgentKind, 'UNKNOWN'>, (host: string, k: IssuedKey) => Step[]> = {
  CHATGPT: (host, k) => [
    { text: 'Build the PasStar plugin with this address and key (from the repository root):',
      code: `THE_PASS_AGENT_KEY=${k.key} plugin/build.sh ${host}` },
    { text: 'In ChatGPT, upload plugin/passtar.zip as a plugin and start a new chat with it.' },
    { text: 'Ask “Who are you buying for, and what are my limits?”. It answers with your protocol.' },
  ],
  CLAUDE: (host, k) => [
    { text: 'Add PasStar as an MCP server in Claude Code:',
      code: `claude mcp add --transport http passtar ${host}/mcp --header "X-Agent-Key: ${k.key}"` },
    { text: 'Or load the plugin folder: set THE_PASS_MCP_URL and THE_PASS_AGENT_KEY, then run claude --plugin-dir plugin/passtar.',
      code: `THE_PASS_MCP_URL=${host}/mcp THE_PASS_AGENT_KEY=${k.key} claude --plugin-dir plugin/passtar` },
  ],
  ERP: (host, k) => [
    { text: 'Your system sends the key with every call. Check the connection:',
      code: `curl -s ${host}/v1/agents/${k.agent_id}/protocol -H "X-Agent-Key: ${k.key}"` },
    { text: 'Then create requests and orders through the REST API; the contract is at:', code: `${host}/docs` },
    { text: 'Orders above the automatic limit return BUYER_AUTHORIZATION_REQUIRED: your approval flow signs them and the system sends the signed authorization back.' },
  ],
};

function Faults() {
  const toast = useToast();
  const faults = useAsync(listFaults, []);
  const toggle = async (f: Fault, enabled: boolean) => {
    try {
      const updated = await setFault(f.name, enabled);
      faults.setData(faults.data!.map((x) => (x.name === f.name ? { ...x, enabled: updated.enabled } : x)));
      toast(`${f.name} ${updated.enabled ? 'on' : 'off'}`, 'info');
    } catch (e) {
      toast(describeError(e), 'bad');
    }
  };
  return (
    <section className="card stack">
      <div><h2>Simulate problems</h2><div className="small muted">For demos and tests</div></div>
      {faults.loading ? <Skeleton h={220} /> : faults.error ? <ErrorState error={faults.error} onRetry={faults.reload} /> : (
        <div className="list-rows">
          {faults.data!.map((f) => (
            <label key={f.name} className="row between" style={{ cursor: 'pointer' }}>
              <span><b>{FAULT_TEXT[f.name]}</b><div className="xs muted mono">{f.name}</div></span>
              <Switch checked={f.enabled} label={f.name} onChange={(v) => toggle(f, v)} />
            </label>
          ))}
        </div>
      )}
    </section>
  );
}
