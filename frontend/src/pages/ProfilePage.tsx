import { useEffect, useState } from 'react';
import { Plus, Save, X } from 'lucide-react';
import { getProtocol, listProfiles, updateProfile } from '../api/endpoints';
import { describeError } from '../api/client';
import type { Profile, ProfileBody } from '../api/types';
import { eur } from '../lib/format';
import { useSession } from '../state/session';
import { Banner, Button, ErrorState, PageHead, Segmented, Skeleton, Switch, useAsync, useToast } from '../components/ui';

type Weights = NonNullable<ProfileBody['weights']>;
const W_LABEL: Record<keyof Weights, string> = { price: 'Price', fulfilment: 'On-time delivery', carbon: 'Low carbon', local: 'Local' };

export function ProfilePage() {
  const { workspace } = useSession();
  const profiles = useAsync(() => listProfiles(workspace.buyerId!), [workspace.buyerId]);
  const protocol = useAsync(() => getProtocol(workspace.protocolId!), [workspace.protocolId]);
  const [id, setId] = useState<string | null>(null);
  const current = profiles.data?.find((p) => p.id === id) ?? profiles.data?.[0];

  return (
    <>
      <PageHead title="Buying profile"
        desc="Your usual preferences, applied to every request."
        actions={profiles.data && profiles.data.length > 1 && (
          <select aria-label="Profile" value={current?.id} onChange={(e) => setId(e.target.value)} style={{ width: 'auto' }}>
            {profiles.data.map((p) => <option key={p.id} value={p.id}>{p.body.name}</option>)}
          </select>
        )} />
      {profiles.loading ? <Skeleton h={420} /> : profiles.error ? <ErrorState error={profiles.error} onRetry={profiles.reload} /> : current && (
        <ProfileForm key={current.id} profile={current} limitCents={protocol.data?.body.max_order_cents}
          onSaved={(p) => profiles.setData(profiles.data!.map((x) => (x.id === p.id ? p : x)))} />
      )}
    </>
  );
}

function TagInput({ values, onChange, placeholder, tone }: { values: string[]; onChange: (v: string[]) => void; placeholder: string; tone: 'bad' | 'neutral' }) {
  const [v, setV] = useState('');
  const add = () => {
    const t = v.trim();
    if (t && !values.includes(t)) onChange([...values, t]);
    setV('');
  };
  return (
    <div className="stack sm">
      <div className="chips">
        {values.map((x) => (
          <span key={x} className={`pill tone-${tone}`} style={{ height: 28, paddingRight: 4 }}>
            {x}
            <button className="btn ghost sm icon-btn" style={{ width: 20, height: 20 }} aria-label={`Remove ${x}`} onClick={() => onChange(values.filter((y) => y !== x))}><X size={12} /></button>
          </span>
        ))}
        {!values.length && <span className="small muted">None</span>}
      </div>
      <div className="input-group">
        <input value={v} placeholder={placeholder} onChange={(e) => setV(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }} />
        <Button onClick={add} disabled={!v.trim()} aria-label="Add"><Plus size={15} /></Button>
      </div>
    </div>
  );
}

function ProfileForm({ profile, limitCents, onSaved }: { profile: Profile; limitCents?: number; onSaved: (p: Profile) => void }) {
  const toast = useToast();
  const [b, setB] = useState<ProfileBody>(profile.body);
  const [saving, setSaving] = useState(false);
  const dirty = JSON.stringify(b) !== JSON.stringify(profile.body);
  useEffect(() => setB(profile.body), [profile]);

  const w: Weights = b.weights ?? { price: 0.4, fulfilment: 0.4, carbon: 0.1, local: 0.1 };
  const total = Object.values(w).reduce((a, x) => a + x, 0) || 1;
  const set = (patch: Partial<ProfileBody>) => setB((x) => ({ ...x, ...patch }));

  const save = async () => {
    setSaving(true);
    try {
      onSaved(await updateProfile(profile.id, b));
      toast('Profile saved. Your next request uses it.', 'ok');
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="stack lg">
      <div className="grid two" style={{ alignItems: 'start' }}>
        <section className="card stack">
          <div className="stack sm"><h2>Never</h2><div className="small muted">Hard exclusions: shown in red, never selectable</div></div>
          <div className="field"><span className="label">Brands</span>
            <TagInput tone="bad" values={b.hard_exclusions?.brands ?? []} placeholder="Add a brand, e.g. Arla" onChange={(brands) => set({ hard_exclusions: { ...b.hard_exclusions, brands } })} />
          </div>
          <div className="field"><span className="label">Product IDs</span>
            <TagInput tone="bad" values={b.hard_exclusions?.product_ids ?? []} placeholder="e.g. 464179" onChange={(product_ids) => set({ hard_exclusions: { ...b.hard_exclusions, product_ids } })} />
          </div>
          <div className="xs muted">Need an exception just once? Use “This brand is OK this time” on step ② of a request. It does not change this profile.</div>
        </section>

        <section className="card stack">
          <div className="stack sm"><h2>Prefer</h2><div className="small muted">Soft preferences: only change the ranking</div></div>
          <label className="row between"><span>Prefer large packs</span>
            <Switch checked={!!b.soft_preferences?.prefer_large_packs} label="Prefer large packs" onChange={(v) => set({ soft_preferences: { ...b.soft_preferences, prefer_large_packs: v } })} />
          </label>
          <label className="row between"><span>Prefer Finnish products</span>
            <Switch checked={!!b.soft_preferences?.countries?.includes('fi')} label="Prefer Finnish products"
              onChange={(v) => set({ soft_preferences: { ...b.soft_preferences, countries: v ? ['fi'] : [] } })} />
          </label>
          <label className="row between"><span>Prefer EU organic</span>
            <Switch checked={!!b.soft_preferences?.labels?.includes('EU_ORGANIC_FARMING')} label="Prefer EU organic"
              onChange={(v) => set({ soft_preferences: { ...b.soft_preferences, labels: v ? ['EU_ORGANIC_FARMING'] : [] } })} />
          </label>
        </section>
      </div>

      <div className="grid two" style={{ alignItems: 'start' }}>
        <section className="card stack">
          <div className="stack sm"><h2>What matters most</h2><div className="small muted">Ranking weights, normalised to 100%</div></div>
          {(Object.keys(W_LABEL) as (keyof Weights)[]).map((k) => (
            <div key={k} className="weight">
              <label htmlFor={`w-${k}`} className="small">{W_LABEL[k]}</label>
              <input id={`w-${k}`} type="range" min={0} max={1} step={0.05} value={w[k]} onChange={(e) => set({ weights: { ...w, [k]: Number(e.target.value) } })} />
              <span className="small strong" style={{ textAlign: 'right' }}>{Math.round(((w[k] ?? 0) / total) * 100)}%</span>
            </div>
          ))}
        </section>

        <section className="card stack">
          <div><h2>Defaults</h2><div className="small muted">Pre-filled in the request panel; you can still change them</div></div>
          <div className="field"><label htmlFor="addr">Delivery address</label>
            <input id="addr" value={b.defaults?.delivery_address ?? ''} onChange={(e) => set({ defaults: { ...b.defaults, delivery_address: e.target.value } })} />
          </div>
          <div className="field"><label htmlFor="lead">Usual lead time (days)</label>
            <input id="lead" inputMode="numeric" value={b.defaults?.lead_days ?? ''} onChange={(e) => set({ defaults: { ...b.defaults, lead_days: e.target.value ? Number(e.target.value.replace(/\D/g, '')) : null } })} />
          </div>
          <div className="field"><span className="label">Substitutes</span>
            <Segmented label="Default substitutes" value={b.defaults?.substitution ?? 'SAME_CATEGORY'}
              options={[['SAME_CATEGORY', 'Same category OK'], ['NONE', 'None'], ['ANY', 'Any']]}
              onChange={(v) => set({ defaults: { ...b.defaults, substitution: v } })} />
          </div>
        </section>
      </div>

      <Banner tone="info" title="The protocol always wins">
        Spending limits, allowed categories and substitution rules come from the signed protocol{limitCents ? ` (hard maximum ${eur(limitCents)})` : ''}. Anything here that is wider than the protocol is ignored.
      </Banner>

      <div className="row">
        <span className="small muted">{dirty ? 'Unsaved changes' : `Saved · ${new Date(profile.updated_at).toLocaleString('en-GB')}`}</span>
        <Button variant="ghost" className="ml-auto" disabled={!dirty} onClick={() => setB(profile.body)}>Discard</Button>
        <Button variant="primary" disabled={!dirty || !b.name} loading={saving} onClick={save}><Save size={15} /> Save profile</Button>
      </div>
    </div>
  );
}
