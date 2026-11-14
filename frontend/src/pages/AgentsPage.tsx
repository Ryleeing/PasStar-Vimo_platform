import { useState } from 'react';
import { Bot, FlaskConical } from 'lucide-react';
import { listFaults, setFault } from '../api/endpoints';
import { describeError } from '../api/client';
import type { Fault } from '../api/types';
import { AGENTS } from '../api/mock/data';
import { Button, ErrorState, PageHead, Pill, Skeleton, Switch, useAsync, useToast } from '../components/ui';

const FAULT_TEXT: Record<Fault['name'], string> = {
  PRICE_CHANGE: 'Price changes between quote and re-check',
  PARTIAL_STOCK: 'Only part of the quantity is deliverable',
  TIMEOUT: 'Valio API times out',
  RATE_LIMIT: 'Valio API returns 429',
  EMPTY_RESULT: 'Search returns nothing',
};

export function AgentsPage() {
  const toast = useToast();
  const faults = useAsync(listFaults, []);
  const [blocked, setBlocked] = useState<string[]>([]);

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
    <>
      <PageHead eyebrow="Valio Aimo · operations" title="Agents & faults"
        desc="Who is calling the channel, and switches that simulate Valio API trouble for demos and tests." />
      <div className="grid two" style={{ alignItems: 'start' }}>
        <section className="card stack">
          <div className="row"><div className="icon-tile"><Bot size={18} /></div><div className="grow"><h2>Connected agents</h2><div className="small muted">Last 24 hours</div></div></div>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Agent</th><th>Requests</th><th>Blocked</th><th /></tr></thead>
              <tbody>
                {AGENTS.map((a) => (
                  <tr key={a.id}>
                    <td><b>{a.name}</b><div className="xs muted mono">{a.id}</div></td>
                    <td>{a.requests}</td>
                    <td>{a.blocked ? <Pill tone={a.blocked > 10 ? 'bad' : 'warn'}>{a.blocked}</Pill> : <span className="muted">0</span>}</td>
                    <td>
                      {blocked.includes(a.id) ? <Pill tone="bad">Blocked</Pill> : (
                        <Button size="sm" variant="outline-danger" onClick={() => { setBlocked((b) => [...b, a.id]); toast(`${a.id} blocked`, 'warn'); }}>Block</Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="xs muted">Illustrative: the API has no agents endpoint yet.</div>
        </section>

        <section className="card stack">
          <div className="row"><div className="icon-tile warn"><FlaskConical size={18} /></div><div className="grow"><h2>Fault injection</h2><div className="small muted">Applied in the Valio proxy layer, before the cache</div></div></div>
          {faults.loading ? <Skeleton h={220} /> : faults.error ? <ErrorState error={faults.error} onRetry={faults.reload} /> : (
            <div className="stack sm">
              {faults.data!.map((f) => (
                <label key={f.name} className="row between card inset tight" style={{ cursor: 'pointer' }}>
                  <span><b className="mono small">{f.name}</b><div className="small muted">{FAULT_TEXT[f.name]}</div></span>
                  <Switch checked={f.enabled} label={f.name} onChange={(v) => toggle(f, v)} />
                </label>
              ))}
            </div>
          )}
          <div className="xs muted mono">GET /v1/admin/faults · PUT /v1/admin/faults/{'{name}'}</div>
        </section>
      </div>
    </>
  );
}
