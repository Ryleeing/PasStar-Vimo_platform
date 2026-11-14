import { useState } from 'react';
import { Activity, Link2, ShieldCheck } from 'lucide-react';
import { getAudit, verifyAudit } from '../api/endpoints';
import { describeError } from '../api/client';
import type { AuditVerifyResult } from '../api/types';
import { time } from '../lib/format';
import { useSession } from '../state/session';
import { useFlow } from '../state/flow';
import { Banner, Button, Empty, ErrorState, PageHead, Pill, Skeleton, useAsync, useToast } from '../components/ui';

const actorLabel = (a: string) => a.replace(/^agent:/, 'Agent ').replace(/^user:/, '').replace(/^system$/, 'The Pass');

export function AuditPage() {
  const { account } = useSession();
  const { flow } = useFlow();
  const toast = useToast();
  const valio = account.key === 'valio_reviewer';
  // With a request in progress, show its events; otherwise everything this account may see (the backend
  // filters by role). Re-fetch when the journey moves so new events show up.
  const requestId = flow.request?.request_id;
  const log = useAsync(() => getAudit(requestId),
    [account.key, requestId, flow.request?.attempt, flow.order?.status, Object.keys(flow.decisions).length]);
  const [verify, setVerify] = useState<AuditVerifyResult | null>(null);
  const [checking, setChecking] = useState(false);

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

  return (
    <>
      <PageHead title={valio ? 'Full audit log' : 'Activity log'}
        desc={valio ? 'All buyers and agents, with the data each one was shown.'
          : requestId ? 'Everything that happened for your current request, in order.'
          : 'Everything the agent and your team did, in order.'}
        actions={<Button variant="ghost" onClick={runVerify} loading={checking}><ShieldCheck size={15} /> Check it hasn’t been altered</Button>} />

      {verify && (
        <div style={{ marginBottom: 16 }}>
          {verify.ok
            ? <Banner tone="ok" title="Log is intact">All {verify.entries_checked} entries checked. Nothing was changed or removed.</Banner>
            : <Banner tone="bad" title="Log was altered">Entry #{verify.first_bad_seq} and later can’t be trusted.</Banner>}
        </div>
      )}

      {log.loading ? <Skeleton h={300} /> : log.error ? <ErrorState error={log.error} onRetry={log.reload} /> : !log.data?.length ? (
        <Empty icon={<Activity size={22} />} title="No activity yet">Events appear here as soon as a request is made.</Empty>
      ) : (
        <section className="card">
          <ol className="timeline">
            {log.data.map((e) => (
              <li key={e.seq}>
                <span className="t">#{e.seq}<br />{time(e.at)}</span>
                <div className="body stack sm">
                  <b>{e.summary}</b>
                  <div className="row wrap xs muted" style={{ gap: 8 }}>
                    <span>{actorLabel(e.actor)}</span>
                    {valio && <span className="mono">{e.event}</span>}
                    {valio && <span className="row mono" style={{ gap: 4 }} title={e.hash}><Link2 size={12} />{e.hash.slice(0, 10)}…</span>}
                  </div>
                  {valio && (e.disclosed_fields?.length ?? 0) > 0 && (
                    <div className="evidence">
                      {e.disclosed_fields!.map((f) => (
                        <Pill key={f.field} tone={f.sensitivity === 'L0' ? 'neutral' : f.sensitivity === 'L1' ? 'info' : 'bad'}>{f.field} · {f.sensitivity}</Pill>
                      ))}
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </section>
      )}
    </>
  );
}
