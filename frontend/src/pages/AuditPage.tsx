// Activity log. Two layers: what happened in plain words (grouped by request, current one first), and the
// machine record behind each line (event code, actor id, exact time, disclosed fields, hash) one click away.
// Valio reviewers audit the channel, so for them the technical layer is always visible.
import { useMemo, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Activity, AlertTriangle, ArrowRight, Ban, Bot, Check, ChevronDown, FileSignature, Lock, LockOpen, ShieldCheck, User } from 'lucide-react';
import { getAudit, verifyAudit } from '../api/endpoints';
import { describeError } from '../api/client';
import type { AuditEntry, AuditVerifyResult } from '../api/types';
import { accountByUserId, PAGES_BY_ROLE } from '../config/accounts';
import { dayLabel, num, REASON } from '../lib/format';
import { useSession } from '../state/session';
import { useFlow } from '../state/flow';
import { Banner, Button, Empty, ErrorState, PageHead, Pill, Segmented, Skeleton, useAsync, useToast } from '../components/ui';

type Kind = 'person' | 'agent' | 'system' | 'done' | 'warn' | 'bad';
interface Line { kind: Kind; title: ReactNode; detail?: ReactNode; link?: { to: string; label: string } }

const KIND_ICON: Record<Kind, ReactNode> = {
  person: <User size={14} />,
  agent: <Bot size={14} />,
  system: <ShieldCheck size={14} />,
  done: <Check size={14} strokeWidth={3} />,
  warn: <AlertTriangle size={14} />,
  bad: <Ban size={14} />,
};

const clock = (iso: string) => new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
const exact = (iso: string) => new Date(iso).toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
const dayOf = (iso: string) => {
  const d = new Date(iso);
  const today = new Date();
  const same = d.toDateString() === today.toDateString();
  return `${same ? 'Today · ' : ''}${dayLabel(d.toISOString().slice(0, 10))}`;
};
/** Rule and reason codes from the backend, in words a kitchen understands. */
const RULE_WORDS: Record<string, string> = {
  PROFILE_EXCLUDED_BRAND: 'a brand your buying profile excludes',
  PROFILE_EXCLUDED_VENDOR: 'a supplier your buying profile excludes',
  PROFILE_EXCLUDED_PRODUCT: 'a product your buying profile excludes',
  PROTOCOL_CATEGORY_FORBIDDEN: 'a product type your purchasing rules don’t allow',
  ALLERGEN_CONTAINS: 'it contains an allergen you must avoid',
  ALLERGEN_MAY_CONTAIN: 'it may contain an allergen you must avoid',
  ALLERGEN_UNDECLARED: 'the allergen information is missing',
  NEEDS_QUANTITY: 'how much you need', NEEDS_CATEGORY: 'which product', NEEDS_DELIVERY_DATE: 'the delivery date',
  NEEDS_REQUIREMENTS: 'an unclear requirement',
  AUTO_LIMIT: 'it is above the amount the agent may order on its own', MAX_ORDER: 'it is above your maximum order',
  OUT_OF_SCOPE: 'it was outside the agent’s permissions', PROTOCOL_INVALID: 'the agent had no valid purchasing rules',
  RATE_LIMITED: 'the agent asked too often', PROBE_LIMIT: 'the agent kept checking stock for different amounts',
  AUTHORIZATION_INVALID: 'the approval it brought was not valid',
};
const words = (code?: unknown) => (typeof code === 'string' ? RULE_WORDS[code] ?? code.toLowerCase().replace(/_/g, ' ') : null);
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

const after = (s: string, sep = ': ') => (s.includes(sep) ? s.slice(s.indexOf(sep) + sep.length) : null);
const orderIdOf = (e: AuditEntry) => (typeof e.payload?.order_id === 'string' ? e.payload.order_id : e.summary.match(/\bord_\w+/)?.[0]) ?? null;

export function AuditPage() {
  const { account } = useSession();
  const { flow } = useFlow();
  const toast = useToast();
  const valio = account.key === 'valio_reviewer';
  const currentId = flow.request?.request_id ?? null;
  const [scope, setScope] = useState<'current' | 'all'>('current');
  // Fetch everything this account may see (the backend filters by role); grouping and the scope filter are local.
  const log = useAsync(() => getAudit(),
    [account.key, currentId, flow.request?.attempt, flow.order?.status, Object.keys(flow.decisions).length]);
  const [verify, setVerify] = useState<AuditVerifyResult | null>(null);
  const [checking, setChecking] = useState(false);
  const canSee = (k: string) => PAGES_BY_ROLE[account.key].some((p) => p.key === k);

  const who = (actor: string): string => {
    if (actor === 'system') return 'PasStar';
    if (actor.startsWith('agent:')) return 'Ordering assistant (AI agent)';
    const id = actor.replace(/^user:/, '');
    if (id === account.userId) return 'You';
    if (id === 'buyer') return 'Your team';
    const a = accountByUserId(id);
    return a ? `${a.name}, ${a.title.toLowerCase()}` : id;
  };

  /** Business wording for each event; falls back to the backend summary for anything unknown. */
  const describe = (e: AuditEntry): Line => {
    const isCurrent = !!currentId && e.request_id === currentId;
    const order = orderIdOf(e);
    const orderLink = order && canSee('orders') ? { to: '/orders', label: 'View order' } : undefined;
    switch (e.event) {
      case 'REQUEST_CREATED': {
        const s = isCurrent ? flow.request?.structured : null;
        const what = after(e.summary)
          ?? (s ? `${num(s.quantity.value)} ${s.quantity.unit} ${(s.requirements ?? []).map((r) => (r.label ?? r.code).toLowerCase()).join(', ')} ${(s.category_label ?? '').toLowerCase()} by ${dayLabel(s.delivery_date)}`.replace(/\s+/g, ' ') : null);
        const how = /natural language/i.test(e.summary) ? 'written in your own words' : /panel/i.test(e.summary) ? 'filled in on the form' : null;
        return { kind: 'person', title: 'New request submitted', detail: what ? <>“{what}”{how && <span className="muted"> · {how}</span>}</> : how };
      }
      case 'CANDIDATES_DISCLOSED': {
        const n = e.summary.match(/(\d+)\s+candidates/)?.[1];
        return {
          kind: 'system', title: n ? `Found ${n} matching options from Valio Aimo` : 'Found matching options from Valio Aimo',
          detail: 'Prices and delivery checked for these products only',
          link: isCurrent && canSee('options') ? { to: '/options', label: 'View options' } : undefined,
        };
      }
      case 'NODE_REJECTED': {
        const step = e.summary.match(/Step (\d)/)?.[1];
        const code = e.summary.match(/\(([A-Z_]+)\)/)?.[1];
        return { kind: 'person', title: step ? `Retried step ${step} and found options again` : 'Retried the search', detail: code ? REASON[code] ?? undefined : undefined };
      }
      case 'QUOTE_SIGNED':
        return { kind: 'system', title: 'Price locked for 15 minutes', detail: 'Valio signed the price for the option you chose' };
      case 'ORDER_PLACED':
        return { kind: 'done', title: 'Ordered automatically', detail: /within|passed|AUTO/i.test(e.summary) ? 'Within your auto-approve limit, so no approval was needed' : undefined, link: orderLink };
      case 'APPROVAL_REQUESTED':
        return { kind: 'warn', title: 'Sent for approval', detail: 'Above your auto-approve limit', link: canSee('approvals') ? { to: '/approvals', label: 'Open approvals' } : undefined };
      case 'ORDER_APPROVED':
      case 'ORDER_SELF_APPROVED':
        return { kind: 'done', title: e.event === 'ORDER_SELF_APPROVED' ? 'Approved by the owner and ordered' : 'Approved and ordered', detail: 'Price and stock were checked again before ordering', link: orderLink };
      case 'REQUOTE_REQUIRED':
        return { kind: 'warn', title: 'Price changed during approval', detail: 'Valio issued a new price; it needs to be approved again' };
      case 'APPROVAL_REJECTED':
        return { kind: 'bad', title: 'Approval rejected', detail: after(e.summary) ?? undefined };
      case 'PROTOCOL_SIGNED':
        return { kind: 'person', title: 'Purchasing rules signed', detail: 'Waiting for Valio to confirm' };
      case 'PROTOCOL_CONFIRMED':
        return { kind: 'done', title: 'Purchasing rules confirmed by Valio', detail: 'The new rules are now in force' };
      case 'PROTOCOL_AMENDED':
        return { kind: 'person', title: 'Purchasing rules updated', detail: e.summary };
      case 'PROFILE_UPDATED':
        return { kind: 'person', title: 'Buying profile updated', detail: after(e.summary, '“') ? `“${after(e.summary, '“')}` : undefined };
      case 'REQUEST_NEEDS_INPUT': {
        const fields = Array.isArray(e.payload?.fields) ? (e.payload.fields as string[]) : [];
        const asked = fields.map((f) => words(`NEEDS_${f.toUpperCase()}`)).filter(Boolean).join(' and ');
        return { kind: 'warn', title: 'Asked a question before searching', detail: asked ? `Needed to know ${asked}` : undefined };
      }
      case 'ATTRIBUTE_NOT_IN_CATALOG':
        return { kind: 'warn', title: 'Asked about a requirement the catalogue can’t check',
                 detail: typeof e.payload?.attribute === 'string' ? `“${e.payload.attribute}” is not something Valio’s product data records` : undefined };
      case 'CANDIDATES_BLOCKED': {
        const n = Array.isArray(e.payload?.product_ids) ? e.payload.product_ids.length : 0;
        return { kind: 'system', title: n ? `Left out ${plural(n, 'product')}` : 'Left out some products', detail: `Because of ${words(e.payload?.reason_code) ?? 'your rules'}` };
      }
      case 'GATE_DECIDED':
        return { kind: 'system', title: 'Checked the chosen option against your purchasing rules', detail: after(e.summary) ?? undefined };
      case 'QUOTE_CREATED':
        return { kind: 'system', title: 'Price locked for 15 minutes', detail: 'Valio signed the price for the option you chose' };
      case 'QUOTE_CHANGED':
        return { kind: 'warn', title: 'Price or stock changed before ordering', detail: 'Valio issued a new price' };
      case 'ORDER_HELD': {
        const reason = words(e.payload?.reason_code ?? (Array.isArray(e.payload?.all_reason_codes) ? e.payload.all_reason_codes[0] : null));
        return { kind: 'warn', title: 'Order waiting for approval', detail: reason ? `Because ${reason}` : undefined, link: canSee('approvals') ? { to: '/approvals', label: 'Open approvals' } : undefined };
      }
      case 'ORDER_REJECTED':
        return { kind: 'bad', title: 'Order not placed', detail: 'It breaks your purchasing rules' };
      case 'APPROVAL_CREATED':
        return { kind: 'warn', title: 'Approval requested', link: canSee('approvals') ? { to: '/approvals', label: 'Open approvals' } : undefined };
      case 'APPROVAL_ESCALATED':
        return { kind: 'warn', title: 'Passed on to Valio for review' };
      case 'PROPOSAL_MODIFIED':
        return { kind: 'person', title: 'Approved with changes', detail: after(e.summary) ?? undefined };
      case 'AUTHORIZATION_ACCEPTED':
        return { kind: 'done', title: 'Approval from your own system accepted' };
      case 'AUTHORIZATION_REJECTED':
        return { kind: 'bad', title: 'Approval from your own system refused', detail: 'It was not valid for this order' };
      case 'AGENT_BLOCKED':
        return { kind: 'bad', title: 'Stopped an AI agent', detail: `Because ${words(e.payload?.reason_code) ?? 'it broke the rules'}` };
      case 'PROTOCOL_DRAFTED':
        return { kind: 'person', title: 'Purchasing rules drafted', detail: 'Not in force until signed and confirmed by Valio' };
      case 'PROTOCOL_UPDATED':
        return { kind: 'person', title: 'Draft purchasing rules edited' };
      case 'PROTOCOL_TIGHTENED':
        return { kind: 'person', title: 'Purchasing rules made stricter', detail: 'In force straight away' };
      case 'PROTOCOL_EXPANSION_REQUESTED':
        return { kind: 'warn', title: 'Asked to loosen the purchasing rules', detail: 'Needs a signature and Valio’s confirmation' };
      case 'PROTOCOL_CHANGE_REFUSED':
        return { kind: 'bad', title: 'A change to the purchasing rules was refused', detail: 'It asked for more than the buyer may grant' };
      case 'PROTOCOL_REVOKED':
        return { kind: 'bad', title: 'Purchasing rules withdrawn', detail: 'The agent can no longer order' };
      case 'AGENT_KEY_ISSUED':
        return { kind: 'person', title: 'Gave an AI agent a new key',
                 detail: `${typeof e.payload?.agent_id === 'string' ? e.payload.agent_id : 'The agent'} can now order under ${typeof e.payload?.protocol_id === 'string' ? e.payload.protocol_id : 'your protocol'}; any older key stopped working`,
                 link: canSee('agents') ? { to: '/agents', label: 'View agents' } : undefined };
      case 'AGENT_KEY_REVOKED':
        return { kind: 'person', title: 'Disconnected an AI agent', detail: 'Its key no longer works; its history stays in this log' };
      case 'AGENT_BLOCKED_BY_VALIO':
        return { kind: 'bad', title: 'Valio blocked an AI agent', detail: 'It is refused on Valio’s channel, even with a valid key' };
      case 'AGENT_UNBLOCKED_BY_VALIO':
        return { kind: 'system', title: 'Valio unblocked an AI agent' };
      case 'SEED_LOADED':
        return { kind: 'system', title: 'Demo data loaded' };
      case 'REQUEST_BLOCKED':
        return { kind: 'bad', title: 'Request blocked', detail: e.summary.replace(/^Blocked:\s*/, '') };
      default:
        return { kind: e.actor.startsWith('agent:') ? 'agent' : e.actor === 'system' ? 'system' : 'person', title: e.summary };
    }
  };

  // Group by request: current request first, then earlier ones (newest first), then account-level events.
  const groups = useMemo(() => {
    const all = log.data ?? [];
    const by = new Map<string, AuditEntry[]>();
    for (const e of all) {
      const k = e.request_id ?? '__account';
      by.set(k, [...(by.get(k) ?? []), e]);
    }
    const list = [...by.entries()].map(([id, entries]) => ({ id, entries, last: entries[entries.length - 1].at }));
    list.sort((a, b) => (a.id === currentId ? -1 : b.id === currentId ? 1 : a.id === '__account' ? 1 : b.id === '__account' ? -1 : b.last.localeCompare(a.last)));
    return scope === 'current' && currentId ? list.filter((g) => g.id === currentId) : list;
  }, [log.data, currentId, scope]);

  const runVerify = async () => {
    setChecking(true);
    try {
      setVerify(await verifyAudit());
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setChecking(false);
    }
  };
  const sealed = (seq: number) => (verify ? (verify.ok || (verify.first_bad_seq != null && seq < verify.first_bad_seq) ? 'ok' : 'bad') : null);

  const groupTitle = (id: string) => (id === '__account' ? 'Account and purchasing rules' : id === currentId ? 'Current request' : 'Earlier request');
  const ordered = (entries: AuditEntry[]) => entries.some((e) => ['ORDER_PLACED', 'ORDER_APPROVED', 'ORDER_SELF_APPROVED'].includes(e.event));

  return (
    <>
      <PageHead title={valio ? 'Full audit log' : 'Activity log'}
        desc={valio ? 'All buyers and agents, with the data each one was shown.' : 'What happened with your requests, in plain words. Every line is also kept as a tamper-proof record.'}
        actions={<Button className="edit-req" onClick={runVerify} loading={checking}><ShieldCheck size={16} /> Verify the log</Button>} />

      {verify && (
        <div style={{ marginBottom: 16 }}>
          {verify.ok
            ? <Banner tone="ok" title="The log is intact">All {verify.entries_checked} records match their seal. Nothing was changed or removed. Each line now shows a lock.</Banner>
            : <Banner tone="bad" title="The log was altered">Record #{verify.first_bad_seq} and everything after it can’t be trusted.</Banner>}
        </div>
      )}

      {!valio && currentId && (
        <div style={{ marginBottom: 16 }}>
          <Segmented label="Show" value={scope} onChange={setScope} options={[['current', 'Current request'], ['all', 'All activity']]} />
        </div>
      )}

      {log.loading ? <Skeleton h={300} /> : log.error ? <ErrorState error={log.error} onRetry={log.reload} /> : !groups.length ? (
        <Empty icon={<Activity size={22} />} title="No activity yet">Events appear here as soon as a request is made.</Empty>
      ) : (
        <div className="stack" style={{ gap: 20 }}>
          {groups.map((g) => (
            <section key={g.id} className={`card act-group ${g.id === currentId ? 'current' : ''}`}>
              <header className="act-head">
                <h2>{groupTitle(g.id)}</h2>
                <span className="row" style={{ gap: 8 }}>
                  {g.id !== '__account' && ordered(g.entries) && <Pill tone="ok">Ordered</Pill>}
                  <span className="act-day">{dayOf(g.entries[0].at)}</span>
                </span>
              </header>
              <ol className="act-list">
                {g.entries.map((e) => (
                  <Entry key={e.seq} e={e} line={describe(e)} who={who(e.actor)} valio={valio} sealed={sealed(e.seq)} />
                ))}
              </ol>
            </section>
          ))}
        </div>
      )}
    </>
  );
}

function Entry({ e, line, who, valio, sealed }: { e: AuditEntry; line: Line; who: string; valio: boolean; sealed: 'ok' | 'bad' | null }) {
  const [open, setOpen] = useState(valio);
  return (
    <li className={`act k-${line.kind}`}>
      <span className="act-mark" aria-hidden>{KIND_ICON[line.kind]}</span>
      <div className="act-body">
        <div className="act-top">
          <span className="act-title">{line.title}</span>
          <time className="act-time" dateTime={e.at} title={exact(e.at)}>{clock(e.at)}</time>
        </div>
        {line.detail && <div className="act-detail">{line.detail}</div>}
        <div className="act-meta">
          <span>{who}</span>
          {line.link && <Link to={line.link.to} className="act-link">{line.link.label}<ArrowRight size={13} aria-hidden /></Link>}
          {sealed && (
            <span className={`act-seal ${sealed}`} title={sealed === 'ok' ? 'Matches its seal' : 'Does not match its seal'}>
              {sealed === 'ok' ? <Lock size={12} aria-hidden /> : <LockOpen size={12} aria-hidden />}{sealed === 'ok' ? 'Verified' : 'Altered'}
            </span>
          )}
          {!valio && (
            <button type="button" className="act-tech-toggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
              Technical details<ChevronDown size={13} className="chev" aria-hidden />
            </button>
          )}
        </div>
        {open && (
          <dl className="act-tech">
            <div><dt>Event</dt><dd className="mono">{e.event}</dd></div>
            <div><dt>Actor</dt><dd className="mono">{e.actor}</dd></div>
            <div><dt>Time</dt><dd className="mono" title={e.at}>{exact(e.at)}</dd></div>
            <div><dt>Record</dt><dd className="mono">#{e.seq}{e.request_id ? ` · ${e.request_id}` : ''}</dd></div>
            <div><dt>System note</dt><dd>{e.summary}</dd></div>
            {(e.disclosed_fields?.length ?? 0) > 0 && (
              <div><dt>Data shared</dt><dd className="row wrap" style={{ gap: 6 }}>
                {e.disclosed_fields!.map((f) => (
                  <Pill key={f.field} tone={f.sensitivity === 'L0' ? 'neutral' : f.sensitivity === 'L1' ? 'info' : 'bad'}>{f.field} · {f.sensitivity}</Pill>
                ))}
              </dd></div>
            )}
            <div><dt>Seal</dt><dd className="mono act-hash" title={e.hash}><FileSignature size={12} aria-hidden /> {e.hash.slice(0, 16)}…</dd></div>
          </dl>
        )}
      </div>
    </li>
  );
}
