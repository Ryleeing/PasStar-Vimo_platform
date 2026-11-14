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
  // Re-fetch when the journey moves so new events show up.
  const log = useAsync(() => getAudit(flow.request?.request_id ?? 'req_1'),
    [account.key, flow.request?.attempt, flow.order?.status, Object.keys(flow.decisions).length]);
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
      <PageHead eyebrow="Traceability" title={valio ? 'Full audit log' : 'Activity log'}
        desc={valio
          ? 'Every buyer and agent, including blocked requests and which data fields were disclosed at which sensitivity level.'
          : 'What happened on your organisation’s requests, in order. Entries are append-only and hash-chained.'}
        actions={<Button onClick={runVerify} loading={checking}><ShieldCheck size={15} /> Verify hash chain</Button>} />

      {verify && (
        <div style={{ marginBottom: 16 }}>
          {verify.ok
            ? <Banner tone="ok" title="Hash chain intact">{verify.entries_checked} entries checked; nothing was altered or removed.</Banner>
            : <Banner tone="bad" title="Hash chain broken">The chain breaks at entry #{verify.first_bad_seq}. Entries after it cannot be trusted.</Banner>}
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
                    <span className="mono">{e.event}</span>
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
          {!valio && <div className="xs muted" style={{ marginTop: 8 }}>Entries about other buyers and Valio-internal checks are not shown to you.</div>}
          <div className="xs muted mono" style={{ marginTop: 8 }}>GET /v1/audit?request_id= · GET /v1/audit/verify</div>
        </section>
      )}
    </>
  );
}
