// Agent console: an external agent you can watch. Left, the conversation as the user sees it in an assistant;
// right, everything PasStar did behind each answer: the key checked at the door, the four steps, the gate, Valio's
// seal, the audit entries. The agent reaches PasStar only through /mcp with its key, like ChatGPT does.
import { useEffect, useRef, useState } from 'react';
import { Bot, Check, ChevronDown, DoorOpen, FileSignature, KeyRound, Maximize2, Minimize2, PlugZap, RotateCcw, ScrollText, Send, ShieldAlert, ShieldCheck, Swords, User } from 'lucide-react';
import { streamConsole, type Behind, type ConsoleEvent } from '../api/console';
import { describeError } from '../api/client';
import { protocolAsAgent } from '../api/endpoints';
import type { AgentConnection } from '../api/types';
import { Markdown } from './Markdown';
import { Button, Pill, Segmented } from './ui';

type Mode = 'llm' | 'script';
type Line = { kind: 'user' | 'assistant' | 'error' | 'attempt'; text: string };
interface Call { id: string; name: string; args: Record<string, unknown>; rest?: boolean; attempt?: string;
                 ok?: boolean; ms?: number; result?: Record<string, unknown>; behind?: Behind }

const DEMO_KEYS: Record<string, string> = { agt_hotel: 'demo-hotel', agt_hotel_ext: 'demo', agt_hospital: 'demo-hospital',
                                            agt_bistro: 'demo-bistro', agt_rogue: 'demo-rogue' };
const UNKNOWN = '__unknown';
const PROMPTS = ['Who are you buying for, and what are my limits?', 'I need 1500 litres of lactose-free milk tomorrow',
                 'Order option 1', 'Is my order approved yet?'];
const STEP_LABEL: Record<string, string> = { UNDERSTAND: 'Understand', FILTER: 'Filter products', RANK: 'Combine & rank', GATE: 'Protocol check' };
const keyStore = (id: string) => `passtar.console.key.${id}`;

/** `start`: open on this agent with this key, already checked (e.g. right after issuing the key). */
export function AgentConsole({ agents, start }: { agents: AgentConnection[]; start?: { agentId: string; key: string } | null }) {
  const [agentId, setAgentId] = useState<string>(() => start?.agentId ?? agents.find((a) => a.has_key && a.protocol_id && a.status === 'ACTIVE')?.agent_id ?? agents[0]?.agent_id ?? UNKNOWN);
  const agent = agents.find((a) => a.agent_id === agentId);
  const demoKey = agent && DEMO_KEYS[agent.agent_id]?.slice(0, 8) === agent.key_hint ? DEMO_KEYS[agent.agent_id] : '';
  const [key, setKey] = useState('');
  const [mode, setMode] = useState<Mode>('llm');
  const [lines, setLines] = useState<Line[]>([]);
  const [calls, setCalls] = useState<Call[]>([]);
  const [history, setHistory] = useState<unknown[]>([]);
  const [state, setState] = useState<Record<string, unknown>>({});
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState<false | 'chat' | 'attacks'>(false);
  const [thinking, setThinking] = useState(false);
  const [present, setPresent] = useState(false);
  // The key is used only once it has been checked against PasStar (Connect). An unknown agent needs no check:
  // watching it being refused is the point.
  const [link, setLink] = useState<{ state: 'idle' | 'checking' | 'ok' | 'bad'; text?: string }>({ state: 'idle' });
  const connected = agentId === UNKNOWN || link.state === 'ok';
  const chatEnd = useRef<HTMLDivElement>(null);
  const behindEnd = useRef<HTMLDivElement>(null);

  // The key for the chosen agent: what was pasted before, else the public demo key if it still works.
  useEffect(() => {
    let saved = '';
    try { saved = sessionStorage.getItem(keyStore(agentId)) ?? ''; } catch { /* private mode */ }
    const k = start && start.agentId === agentId ? start.key : agentId === UNKNOWN ? 'tp_made_up_key_0000000000' : saved || demoKey;
    setKey(k);
    setLink({ state: 'idle' });
    reset();
    if (start && start.agentId === agentId) void check(k);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);
  useEffect(() => { try { if (agentId !== UNKNOWN) sessionStorage.setItem(keyStore(agentId), key); } catch { /* private mode */ } }, [agentId, key]);
  useEffect(() => { chatEnd.current?.scrollIntoView({ block: 'end', behavior: 'smooth' }); }, [lines, thinking]);
  useEffect(() => { behindEnd.current?.scrollIntoView({ block: 'end', behavior: 'smooth' }); }, [calls]);
  useEffect(() => {
    if (!present) return;
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setPresent(false);
    window.addEventListener('keydown', esc);
    return () => window.removeEventListener('keydown', esc);
  }, [present]);

  async function check(k = key) {
    if (!k.trim() || agentId === UNKNOWN) return;
    setLink({ state: 'checking' });
    try {
      const p = await protocolAsAgent(agentId, k.trim());
      const a = agents.find((x) => x.agent_id === agentId);
      setLink({ state: 'ok', text: `Connected as ${agentId} · ${a?.buyer_name ?? p.body.buyer_id} · protocol ${p.id} v${p.version}` });
    } catch (e) {
      setLink({ state: 'bad', text: `Key not accepted: ${describeError(e)}` });
    }
  }

  function reset() {
    setLines([]);
    setCalls([]);
    setHistory([]);
    setState({});
  }

  let attempt: string | undefined;
  const onEvent = (e: ConsoleEvent) => {
    switch (e.type) {
      case 'thinking': setThinking(true); break;
      case 'attack':
        attempt = e.title;
        setLines((l) => [...l, { kind: 'attempt', text: e.title }]);
        break;
      case 'tool_call':
        setThinking(false);
        setCalls((c) => [...c, { id: `${e.id}-${c.length}`, name: e.name, args: e.args, rest: e.rest, attempt }]);
        break;
      case 'tool_result':
        setCalls((c) => {
          const i = c.map((x) => x.name).lastIndexOf(e.name);
          if (i < 0) return c;
          const next = [...c];
          next[i] = { ...next[i], ok: e.ok, ms: e.ms, result: e.result, behind: e.behind };
          return next;
        });
        break;
      case 'assistant': setThinking(false); setLines((l) => [...l, { kind: 'assistant', text: e.text }]); break;
      case 'error': setThinking(false); setLines((l) => [...l, { kind: 'error', text: e.message }]); break;
      case 'done':
        if (e.history) setHistory(e.history);
        if (e.state) setState(e.state);
        break;
    }
  };

  const send = async (text = input) => {
    const t = text.trim();
    if (!t || busy) return;
    setInput('');
    setLines((l) => [...l, { kind: 'user', text: t }]);
    setBusy('chat');
    try {
      await streamConsole('/v1/console/turn', { agent_key: key, text: t, mode, history, state }, onEvent);
    } catch (err) {
      setLines((l) => [...l, { kind: 'error', text: describeError(err) }]);
    } finally {
      setBusy(false);
      setThinking(false);
    }
  };

  const attack = async () => {
    if (busy) return;
    setLines((l) => [...l, { kind: 'user', text: 'Try to break the rules.' }]);
    setBusy('attacks');
    try {
      await streamConsole('/v1/console/attacks', { agent_key: key }, onEvent);
      setLines((l) => [...l, { kind: 'assistant', text: 'Every attempt was refused by PasStar. The details are on the right.' }]);
    } catch (err) {
      setLines((l) => [...l, { kind: 'error', text: describeError(err) }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`console ${present ? 'presenting' : ''}`}>
      <div className="console-bar">
        <label className="console-field">
          <span>Agent</span>
          <select value={agentId} onChange={(e) => setAgentId(e.target.value)} disabled={!!busy}>
            {agents.map((a) => <option key={a.agent_id} value={a.agent_id}>{a.name}{a.status === 'BLOCKED' ? ' (blocked by Valio)' : !a.protocol_id ? ' (no protocol)' : !a.has_key ? ' (disconnected)' : ''}</option>)}
            <option value={UNKNOWN}>An unknown agent (made-up key)</option>
          </select>
        </label>
        <div className="console-field grow">
          <span>Its key</span>
          {link.state === 'ok' ? (
            <div className="console-key ok">
              <span className="mono">{key.slice(0, 8)}…</span>
              <Button size="sm" variant="ghost" onClick={() => { setLink({ state: 'idle' }); reset(); }} disabled={!!busy}><KeyRound size={14} /> Change key</Button>
            </div>
          ) : (
            <form className="console-key" onSubmit={(e) => { e.preventDefault(); void check(); }}>
              <input className="mono" value={key} onChange={(e) => { setKey(e.target.value); setLink({ state: 'idle' }); }}
                disabled={!!busy || agentId === UNKNOWN} placeholder="Paste the key you issued to this agent" spellCheck={false} aria-label="Agent key" />
              {agentId !== UNKNOWN && (
                <Button size="sm" variant="primary" type="submit" loading={link.state === 'checking'} disabled={!key.trim()}><PlugZap size={14} /> Connect</Button>
              )}
            </form>
          )}
        </div>
        <Segmented label="Mode" value={mode} onChange={(m) => { setMode(m); reset(); }}
          options={[['llm', 'Live model'], ['script', 'Script']]} />
        <div className="row" style={{ gap: 8 }}>
          <Button size="sm" variant="outline-danger" onClick={attack} disabled={!!busy || !connected} loading={busy === 'attacks'}><Swords size={15} /> Try to break the rules</Button>
          <Button size="sm" variant="ghost" onClick={reset} disabled={!!busy}><RotateCcw size={15} /> Start over</Button>
          <Button size="sm" variant="ghost" onClick={() => setPresent((p) => !p)} aria-pressed={present}>
            {present ? <><Minimize2 size={15} /> Exit</> : <><Maximize2 size={15} /> Present</>}
          </Button>
        </div>
      </div>

      {link.text && (
        <div className={`console-link ${link.state}`} role="status">
          {link.state === 'ok' ? <Check size={15} aria-hidden /> : <ShieldAlert size={15} aria-hidden />} {link.text}
        </div>
      )}
      {agentId === UNKNOWN && (
        <div className="console-link bad" role="status"><ShieldAlert size={15} aria-hidden /> An agent with a made-up key: PasStar should refuse every call.</div>
      )}

      <div className="console-split">
        {/* ── What the user sees ── */}
        <section className="console-chat card" aria-label="Conversation">
          <header className="console-head"><Bot size={16} aria-hidden /> <b>What the user sees</b><span className="muted small">a simulated external agent</span></header>
          <div className="console-scroll">
            {!lines.length && (
              <div className="console-empty">
                <p className="muted">{connected ? 'Talk to the agent as a kitchen would. It can only reach PasStar with its key.' : 'Connect the agent with its key above to start.'}</p>
                <div className="chips">
                  {PROMPTS.map((p) => <button key={p} type="button" className="chip" onClick={() => send(p)} disabled={!!busy || !connected}>{p}</button>)}
                </div>
              </div>
            )}
            {lines.map((l, i) => (
              <div key={i} className={`msg msg-${l.kind}`}>
                {l.kind === 'user' && <span className="msg-who" aria-hidden><User size={14} /></span>}
                {l.kind === 'assistant' && <span className="msg-who" aria-hidden><Bot size={14} /></span>}
                {l.kind === 'attempt' ? <span className="msg-attempt"><Swords size={14} aria-hidden /> Attempt: {l.text}</span>
                  : <div className="msg-text">{l.kind === 'assistant' ? <Markdown text={l.text} /> : l.text}</div>}
              </div>
            ))}
            {thinking && <div className="msg msg-assistant"><span className="msg-who" aria-hidden><Bot size={14} /></span><div className="msg-text muted">Thinking<span className="run-dots" aria-hidden><i /><i /><i /></span></div></div>}
            <div ref={chatEnd} />
          </div>
          <form className="console-input" onSubmit={(e) => { e.preventDefault(); send(); }}>
            <input value={input} onChange={(e) => setInput(e.target.value)} placeholder={connected ? 'Message the agent…' : 'Connect the agent with its key first'}
              disabled={!!busy || !connected} aria-label="Message the agent" />
            <Button variant="primary" size="sm" type="submit" disabled={!!busy || !input.trim() || !connected} loading={busy === 'chat'}><Send size={15} /> Send</Button>
          </form>
        </section>

        {/* ── What PasStar did ── */}
        <section className="console-behind card" aria-label="Behind the scenes">
          <header className="console-head"><ScrollText size={16} aria-hidden /> <b>Behind the scenes</b><span className="muted small">what PasStar did for each call</span></header>
          <div className="console-scroll">
            {!calls.length && <p className="muted console-empty">Each call the agent makes appears here: the key checked at the door, the steps PasStar ran, the rules it applied and what it wrote to the log.</p>}
            {calls.map((c, i) => <CallCard key={c.id} c={c} first={i === 0 || calls[i - 1].attempt !== c.attempt} />)}
            <div ref={behindEnd} />
          </div>
        </section>
      </div>
    </div>
  );
}

function CallCard({ c, first }: { c: Call; first: boolean }) {
  const [raw, setRaw] = useState(false);
  const b = c.behind;
  const pending = c.ok === undefined;
  const args = Object.keys(c.args ?? {}).length ? JSON.stringify(c.args) : null;
  return (
    <>
      {c.attempt && first && <div className="call-attempt"><Swords size={14} aria-hidden /> {c.attempt}</div>}
      <article className={`call ${pending ? 'pending' : c.ok ? 'ok' : 'refused'}`}>
        <header className="call-head">
          <span className="mono call-name">{c.rest ? c.name : `${c.name}()`}</span>
          <span className="muted small">{c.rest ? 'REST' : 'MCP'}{c.ms != null && ` · ${c.ms} ms`}</span>
          <span className="ml-auto">{pending ? <Pill tone="neutral">…</Pill> : c.ok ? <Pill tone="ok">Answered</Pill> : <Pill tone="bad">Refused</Pill>}</span>
        </header>
        {args && <div className="call-args mono">{args}</div>}

        {b && (
          <div className="call-body">
            <div className="call-row">
              <DoorOpen size={15} aria-hidden />
              {b.identity.agent_id
                ? <span>Key checked: <b>{b.identity.agent_id}</b> for {b.identity.buyer_id} · sees up to <b>{b.identity.sensitivity}</b> (L2 stock levels and L3 costs withheld)</span>
                : <span className="bad-text">{b.identity.note}</span>}
            </div>
            {b.refused && (
              <div className="call-row bad-text">
                <ShieldAlert size={15} aria-hidden />
                <span><b>{b.refused.code}</b> · {b.refused.message}{b.refused.reported_to_valio && <> · <Pill tone="bad">Reported to Valio</Pill></>}</span>
              </div>
            )}
            {b.mandate && <div className="call-row"><FileSignature size={15} aria-hidden /><span>{b.mandate}</span></div>}
            {b.steps && (
              <ol className="call-steps">
                {b.steps.map((s) => (
                  <li key={s.node}><span className="call-step-n">{s.node}</span><div><b>{STEP_LABEL[s.name] ?? s.name}</b><p>{s.summary}</p></div></li>
                ))}
              </ol>
            )}
            {b.options && <div className="call-row"><Check size={15} aria-hidden /><span>{b.options.shown} options returned{b.options.left_out ? `, ${b.options.left_out} products left out by the rules` : ''}</span></div>}
            {b.gate && (
              <div className="call-row">
                <ShieldCheck size={15} aria-hidden />
                <span>Gate: <b>{b.gate.status}</b>{b.gate.summary && <> · {b.gate.summary}</>}</span>
              </div>
            )}
            {b.seal && (
              <div className={`call-row ${b.seal.signature_valid ? 'ok-text' : 'bad-text'}`}>
                <FileSignature size={15} aria-hidden />
                <span>Valio’s signature {b.seal.signature_valid ? 'is valid' : 'does NOT match'} ({b.seal.algorithm}, key {b.seal.key_id})</span>
              </div>
            )}
            {b.audit.length > 0 && (
              <div className="call-audit">
                <div className="small muted">Written to the tamper-proof log</div>
                {b.audit.slice(0, 6).map((a) => (
                  <div key={a.seq} className="call-audit-row"><span className="mono">#{a.seq}</span> <b>{a.event}</b> <span className="muted">{a.summary}</span></div>
                ))}
                {b.audit.length > 6 && <div className="small muted">and {b.audit.length - 6} more</div>}
              </div>
            )}
            {c.result && (
              <button type="button" className="act-tech-toggle" aria-expanded={raw} onClick={() => setRaw((r) => !r)}>
                Raw response<ChevronDown size={13} className="chev" aria-hidden />
              </button>
            )}
            {raw && <pre className="call-raw mono">{JSON.stringify(c.result, null, 2)}</pre>}
          </div>
        )}
      </article>
    </>
  );
}

