// Buying profile, laid out the way the agent uses it:
//   1. Never include  → filters products out before anything else.
//   2. How options are ranked → one place for everything that changes the order (priorities + tie-breakers).
//   3. Request defaults → pre-filled on new requests.
// Alongside: the approval rules from the signed protocol, read-only, so the whole rule set is visible here.
import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Check, Lock, Plus, RotateCcw, Save, Search, X } from 'lucide-react';
import { WeightDonut } from '../components/WeightDonut';
import { getProtocol, listProfiles, updateProfile } from '../api/endpoints';
import { describeError } from '../api/client';
import type { Profile, ProfileBody, Protocol } from '../api/types';
import { accountByUserId, PAGES_BY_ROLE } from '../config/accounts';
import { eur } from '../lib/format';
import { useFlow } from '../state/flow';
import { useSession } from '../state/session';
import { Banner, Button, ErrorState, PageHead, Skeleton, Switch, useAsync, useToast } from '../components/ui';

type Weights = NonNullable<ProfileBody['weights']>;
type WKey = keyof Weights;
const PRIORITIES: { key: WKey; label: string; help: string; color: string }[] = [
  { key: 'fulfilment', label: 'On-time delivery', help: 'Deliverable in full on your date', color: 'var(--seg-1)' },
  { key: 'price', label: 'Price', help: 'Lower total, VAT included', color: 'var(--seg-2)' },
  { key: 'local', label: 'Local origin', help: 'Made in the countries you choose below', color: 'var(--seg-3)' },
  { key: 'carbon', label: 'Low carbon', help: 'Lower CO₂ footprint per kg', color: 'var(--seg-4)' },
];
const PRESETS: { label: string; w: Record<WKey, number> }[] = [
  { label: 'Balanced', w: { fulfilment: 40, price: 40, local: 10, carbon: 10 } },
  { label: 'Most reliable', w: { fulfilment: 60, price: 20, local: 10, carbon: 10 } },
  { label: 'Lowest cost', w: { fulfilment: 20, price: 60, local: 10, carbon: 10 } },
  { label: 'Greener & local', w: { fulfilment: 30, price: 20, local: 25, carbon: 25 } },
];
const SUBSTITUTES: { value: 'SAME_CATEGORY' | 'NONE' | 'ANY'; label: string; help: string }[] = [
  { value: 'NONE', label: 'Exact product type only', help: 'Only what you asked for. Fewer options, no surprises.' },
  { value: 'SAME_CATEGORY', label: 'Same category OK', help: 'A similar product from the same category, e.g. another lactose-free milk. Your requirements still apply.' },
  { value: 'ANY', label: 'Any category', help: 'Products from other categories too. Your requirements still apply.' },
];
const COUNTRIES: [string, string][] = [['fi', 'Finland'], ['se', 'Sweden'], ['ee', 'Estonia']];

/** Weights as whole points out of 100 (exact values survive a save). */
function toPoints(w?: Weights | null): Record<WKey, number> {
  const base = w ?? { price: 0.4, fulfilment: 0.4, carbon: 0.1, local: 0.1 };
  const total = Object.values(base).reduce((a, x) => a + x, 0) || 1;
  const p = Object.fromEntries((Object.keys(base) as WKey[]).map((k) => [k, Math.round((base[k] / total) * 100)])) as Record<WKey, number>;
  const diff = 100 - Object.values(p).reduce((a, x) => a + x, 0);
  if (diff) { const top = (Object.keys(p) as WKey[]).sort((a, b) => p[b] - p[a])[0]; p[top] += diff; }
  return p;
}

export function ProfilePage() {
  const { workspace, account } = useSession();
  const profiles = useAsync(() => listProfiles(workspace.buyerId!), [workspace.buyerId]);
  const protocol = useAsync(() => getProtocol(workspace.protocolId!), [workspace.protocolId]);
  const [id, setId] = useState<string | null>(null);
  const current = profiles.data?.find((p) => p.id === id) ?? profiles.data?.[0];
  const canSeeProtocol = PAGES_BY_ROLE[account.key].some((p) => p.key === 'protocol');

  return (
    <>
      <PageHead title="Buying profile"
        desc="How the assistant picks products for you. A request uses the profile chosen when you make it."
        actions={profiles.data && profiles.data.length > 1 && (
          <label className="profile-pick">
            <span>Profile</span>
            <select aria-label="Profile" value={current?.id} onChange={(e) => setId(e.target.value)}>
              {profiles.data.map((p) => <option key={p.id} value={p.id}>{p.body.name}</option>)}
            </select>
          </label>
        )} />
      {profiles.loading ? <Skeleton h={420} /> : profiles.error ? <ErrorState error={profiles.error} onRetry={profiles.reload} /> : current && (
        <ProfileForm key={current.id} profile={current} protocol={protocol.data ?? null} canSeeProtocol={canSeeProtocol}
          onSaved={(p) => profiles.setData(profiles.data!.map((x) => (x.id === p.id ? p : x)))} />
      )}
    </>
  );
}

function ProfileForm({ profile, protocol, canSeeProtocol, onSaved }: {
  profile: Profile; protocol: Protocol | null; canSeeProtocol: boolean; onSaved: (p: Profile) => void;
}) {
  const toast = useToast();
  const navigate = useNavigate();
  const { flow } = useFlow();
  const [b, setB] = useState<ProfileBody>(profile.body);
  const [saving, setSaving] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  const dirty = JSON.stringify(b) !== JSON.stringify(profile.body);
  useEffect(() => setB(profile.body), [profile]);
  const set = (patch: Partial<ProfileBody>) => { setJustSaved(false); setB((x) => ({ ...x, ...patch })); };

  // ── priorities: 100 points shared on the donut ──
  const [points, setPoints] = useState<Record<WKey, number>>(() => toPoints(profile.body.weights));
  useEffect(() => setPoints(toPoints(profile.body.weights)), [profile]);
  const used = Object.values(points).reduce((a, x) => a + x, 0);
  const applyPoints = (p: Record<WKey, number>) => {
    setPoints(p);
    const sum = Object.values(p).reduce((a, x) => a + x, 0) || 1;
    set({ weights: Object.fromEntries((Object.keys(p) as WKey[]).map((k) => [k, +(p[k] / sum).toFixed(4)])) as Weights });
  };
  const preset = PRESETS.find((p) => (Object.keys(p.w) as WKey[]).every((k) => p.w[k] === points[k]));

  const countries = b.soft_preferences?.countries ?? [];
  const setCountries = (c: string[]) => set({ soft_preferences: { ...b.soft_preferences, countries: c } });

  // Known products and brands from the current search: suggestions instead of blind typing.
  const known = useMemo(() => {
    const r = flow.request;
    const lines = [...(r?.candidates ?? []), ...(r?.blocked ?? [])].map((c) => c.lines[0]);
    const products = new Map(lines.map((l) => [l.product_id, l.name]));
    const brands = [...new Set(lines.map((l) => l.brand).filter((x): x is string => !!x))];
    return { products, brands };
  }, [flow.request]);

  const save = async () => {
    setSaving(true);
    try {
      onSaved(await updateProfile(profile.id, b));
      setJustSaved(true);
      toast('Profile saved', 'ok');
    } catch (e) {
      toast(describeError(e), 'bad');
    } finally {
      setSaving(false);
    }
  };
  const discard = () => { setB(profile.body); setPoints(toPoints(profile.body.weights)); };

  const pb = protocol?.body;
  const brands = b.hard_exclusions?.brands ?? [];
  const productIds = b.hard_exclusions?.product_ids ?? [];
  const sub = b.defaults?.substitution ?? 'SAME_CATEGORY';
  const prefs = b.soft_preferences ?? {};

  return (
    <div className="prof">
      {justSaved && flow.request && (
        <Banner tone="info" title="Saved. Your current options still use the old settings."
          action={<Button size="sm" onClick={() => navigate('/request')}>Search again</Button>}>
          New requests use this profile straight away.
        </Banner>
      )}

      <div className="prof-grid">
        <div className="prof-main">
          {/* 1 · exclusions */}
          <section className="card prof-card">
            <header className="prof-head">
              <h2>Never include</h2>
              <p>Filtered out before anything is ranked. Shown as “left out” in your options.</p>
            </header>

            <div className="field">
              <span className="label">Brands</span>
              <Picker placeholder="Search brands" values={brands} display={(x) => x}
                suggestions={known.brands} allowFree={(q) => q.length >= 2} freeHint="Add brand, not seen in your searches yet:"
                onChange={(v) => set({ hard_exclusions: { ...b.hard_exclusions, brands: v } })} />
            </div>

            <div className="field">
              <span className="label">Products</span>
              <Picker placeholder="Search by product name or number" values={productIds}
                display={(pid) => known.products.get(pid) ?? `Product ${pid}`} sub={(pid) => `No. ${pid}`}
                suggestions={[...known.products.keys()]} match={(pid, q) => `${known.products.get(pid) ?? ''} ${pid}`.toLowerCase().includes(q)}
                allowFree={(q) => /^\d{4,}$/.test(q)} freeHint="Add product number"
                onChange={(v) => set({ hard_exclusions: { ...b.hard_exclusions, product_ids: v } })} />
            </div>

            <p className="prof-note">Need an exception once? While reviewing options, open “How these options were found”, choose <b>Adjust</b> on “Filter products”, then <b>Allow a brand this time</b>. This profile stays as it is.</p>
          </section>

          {/* 2 · ranking */}
          <section className="card prof-card">
            <header className="prof-head">
              <h2>How options are ranked</h2>
              <p>Share 100 points between what matters. Options are ordered by this score.</p>
            </header>

            <div className="presets" role="group" aria-label="Quick presets">
              {PRESETS.map((p) => (
                <button key={p.label} type="button" className="chip" aria-pressed={preset?.label === p.label} onClick={() => applyPoints({ ...p.w })}>
                  {preset?.label === p.label && <Check size={14} aria-hidden />}{p.label}
                </button>
              ))}
            </div>

            <WeightDonut segs={PRIORITIES} values={PRIORITIES.map((p) => points[p.key])}
              onChange={(v) => applyPoints(Object.fromEntries(PRIORITIES.map((p, i) => [p.key, v[i]])) as Record<WKey, number>)} />

            <div className="field">
              <span className="label">“Local” means made in</span>
              <div className="chips">
                {COUNTRIES.map(([code, name]) => {
                  const on = countries.includes(code);
                  return (
                    <button key={code} type="button" className="chip" aria-pressed={on}
                      onClick={() => setCountries(on ? countries.filter((c) => c !== code) : [...countries, code])}>
                      {on && <Check size={14} aria-hidden />}{name}
                    </button>
                  );
                })}
              </div>
              {!countries.length && points.local > 0 && <span className="help warn-text">Choose a country, otherwise “Local origin” has no effect.</span>}
            </div>

            <div className="tiebreak">
              <div className="tiebreak-head">
                <span className="label">Tie-breakers</span>
                <span className="help">Only used when two products are otherwise equal</span>
              </div>
              <div className="tb-row">
                <span>Prefer larger packs</span>
                <Switch checked={!!prefs.prefer_large_packs} label="Prefer larger packs"
                  onChange={(v) => set({ soft_preferences: { ...prefs, prefer_large_packs: v } })} />
              </div>
              <div className="tb-row">
                <span>Prefer EU organic</span>
                <Switch checked={!!prefs.labels?.includes('EU_ORGANIC_FARMING')} label="Prefer EU organic"
                  onChange={(v) => set({ soft_preferences: { ...prefs, labels: v ? [...(prefs.labels ?? []), 'EU_ORGANIC_FARMING'] : (prefs.labels ?? []).filter((x) => x !== 'EU_ORGANIC_FARMING') } })} />
              </div>
            </div>
          </section>
        </div>

        <aside className="prof-side">
          {/* 3 · defaults */}
          <section className="card prof-card">
            <header className="prof-head">
              <h2>Request defaults</h2>
              <p>Pre-filled on new requests. You can change them each time.</p>
            </header>
            <div className="field"><label htmlFor="addr">Delivery address</label>
              <input id="addr" value={b.defaults?.delivery_address ?? ''} placeholder="e.g. loading dock"
                onChange={(e) => set({ defaults: { ...b.defaults, delivery_address: e.target.value || null } })} />
            </div>
            <div className="field"><label htmlFor="lead">Usual lead time</label>
              <div className="input-suffix">
                <input id="lead" inputMode="numeric" value={b.defaults?.lead_days ?? ''}
                  onChange={(e) => set({ defaults: { ...b.defaults, lead_days: e.target.value ? Number(e.target.value.replace(/\D/g, '')) : null } })} />
                <span>days</span>
              </div>
            </div>
            <fieldset className="field radio-cards">
              <legend className="label">If the exact product isn’t available</legend>
              {SUBSTITUTES.map((o) => (
                <label key={o.value} className={`radio-card ${sub === o.value ? 'on' : ''}`}>
                  <input type="radio" name="substitution" value={o.value} checked={sub === o.value}
                    onChange={() => set({ defaults: { ...b.defaults, substitution: o.value } })} />
                  <span><b>{o.label}</b><span className="help">{o.help}</span></span>
                </label>
              ))}
            </fieldset>
          </section>

          {/* read-only: approval rules */}
          <section className="card prof-card rules">
            <header className="prof-head">
              <h2><Lock size={16} aria-hidden /> Approval rules</h2>
              <p>From the purchasing rules signed with Valio. Not changed here.</p>
            </header>
            {pb ? (
              <dl className="rule-list">
                <div><dt>Orders automatically up to</dt><dd>{eur(pb.auto_approve_limit_cents)} <span>per order</span></dd></div>
                <div><dt>Above that, approved by</dt><dd>{pb.approval_mode === 'SELF' ? <>You <span>(logged)</span></> : <>{accountByUserId(pb.approver)?.name ?? pb.approver} <span>({accountByUserId(pb.approver)?.title.toLowerCase()})</span></>}</dd></div>
                <div><dt>Never more than</dt><dd>{eur(pb.max_order_cents)} <span>per order</span></dd></div>
              </dl>
            ) : <Skeleton h={90} />}
            <p className="prof-note">If this profile asks for something the rules don’t allow, the rules win.{canSeeProtocol && <> <Link to="/protocol">View purchasing rules</Link></>}</p>
          </section>
        </aside>
      </div>

      {dirty ? (
        <div className="savebar" role="status">
          <span className="savebar-state">Unsaved changes</span>
          <Button variant="ghost" onClick={discard}><RotateCcw size={15} /> Discard</Button>
          <Button variant="primary" disabled={!b.name || used !== 100} title={used !== 100 ? 'Points must add up to 100' : undefined} loading={saving} onClick={save}><Save size={15} /> Save profile</Button>
        </div>
      ) : (
        <p className="saved-state" role="status"><Check size={15} aria-hidden /> All changes saved · {new Date(profile.updated_at).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}</p>
      )}
    </div>
  );
}

/** Search-and-pick with chips. Suggestions come from known data; free entry only where it's safe. */
function Picker({ values, onChange, suggestions, display, sub, match, placeholder, allowFree, freeHint }: {
  values: string[]; onChange: (v: string[]) => void; suggestions: string[]; display: (v: string) => string; sub?: (v: string) => string;
  match?: (v: string, q: string) => boolean; placeholder: string; allowFree?: boolean | ((q: string) => boolean); freeHint: string;
}) {
  const [q, setQ] = useState('');
  const [focus, setFocus] = useState(false);
  const query = q.trim().toLowerCase();
  const hits = suggestions.filter((s) => !values.includes(s) && (!query || (match ? match(s, query) : display(s).toLowerCase().includes(query)))).slice(0, 6);
  const exact = suggestions.find((s) => display(s).toLowerCase() === query);
  const freeOk = !!query && !exact && !values.some((v) => v.toLowerCase() === query) && (typeof allowFree === 'function' ? allowFree(q.trim()) : !!allowFree);
  const add = (v: string) => { if (!values.includes(v)) onChange([...values, v]); setQ(''); };

  return (
    <div className="picker">
      {values.length > 0 && (
        <div className="chips">
          {values.map((x) => (
            <span key={x} className="pick-chip">
              <span>{display(x)}{sub && <small>{sub(x)}</small>}</span>
              <button type="button" aria-label={`Remove ${display(x)}`} onClick={() => onChange(values.filter((y) => y !== x))}><X size={13} /></button>
            </span>
          ))}
        </div>
      )}
      <div className="picker-box">
        <Search size={15} aria-hidden />
        <input value={q} placeholder={placeholder} onChange={(e) => setQ(e.target.value)}
          onFocus={() => setFocus(true)} onBlur={() => setTimeout(() => setFocus(false), 150)}
          onKeyDown={(e) => {
            if (e.key !== 'Enter') return;
            e.preventDefault();
            if (exact && !values.includes(exact)) add(exact);
            else if (hits.length === 1) add(hits[0]);
            else if (freeOk) add(q.trim());
          }} />
      </div>
      {focus && (hits.length > 0 || freeOk || (query && !hits.length)) && (
        <ul className="picker-menu" role="listbox">
          {hits.map((s) => (
            <li key={s}><button type="button" role="option" aria-selected={false} onMouseDown={(e) => e.preventDefault()} onClick={() => add(s)}>
              {display(s)}{sub && <small>{sub(s)}</small>}
            </button></li>
          ))}
          {freeOk && (
            <li><button type="button" className="free" onMouseDown={(e) => e.preventDefault()} onClick={() => add(q.trim())}><Plus size={14} aria-hidden />{freeHint} “{q.trim()}”</button></li>
          )}
          {query && !hits.length && !freeOk && <li className="none">No match. Check the spelling.</li>}
        </ul>
      )}
    </div>
  );
}
