// Interactive donut for sharing 100 points between ranking priorities.
//  · Drag a divider between two segments: only those two change, in 5-point steps.
//  · Any exact value (e.g. 33) can be typed in the legend. If the total isn't 100, the donut shows it and
//    offers to balance the others; dragging resumes once the total is 100.
//  · Dividers are keyboard sliders too (arrow keys move 5 points).
//  · The ring stays exactly as you left it (no re-ordering, no rotation). Only the legend on the right
//    re-orders from highest to lowest when a drag or edit ends; nothing moves while you are still typing.
import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import { Scale } from 'lucide-react';

export interface DonutSeg { key: string; label: string; help: string; color: string }

const SNAP = 5;
const CX = 120, CY = 120, R_OUT = 102, R_IN = 80, R_MID = (R_OUT + R_IN) / 2;
const pt = (deg: number, r: number) => {
  const a = (deg * Math.PI) / 180;
  return [CX + r * Math.sin(a), CY - r * Math.cos(a)] as const;
};
function sector(a0: number, a1: number) {
  const gap = a1 - a0 >= 359.9 ? 0 : 0.8;
  const s = a0 + gap, e = a1 - gap;
  if (e <= s) return '';
  const large = e - s > 180 ? 1 : 0;
  const [x0, y0] = pt(s, R_OUT), [x1, y1] = pt(e, R_OUT), [x2, y2] = pt(e, R_IN), [x3, y3] = pt(s, R_IN);
  return `M${x0} ${y0} A${R_OUT} ${R_OUT} 0 ${large} 1 ${x1} ${y1} L${x2} ${y2} A${R_IN} ${R_IN} 0 ${large} 0 ${x3} ${y3}Z`;
}
const snap = (v: number) => Math.round(v / SNAP) * SNAP;
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
/** Indices sorted by value, highest first; ties keep their current order. */
const byValue = (vals: number[], prev: number[]) => [...prev].sort((a, b) => vals[b] - vals[a]);

export function WeightDonut({ segs: rawSegs, values: rawValues, onChange: onRawChange }: { segs: DonutSeg[]; values: number[]; onChange: (v: number[]) => void }) {
  // The ring keeps a fixed order; only the legend is sorted (by value, highest first).
  const segs = rawSegs;
  const values = rawValues;
  const [order, setOrder] = useState<number[]>(() => byValue(rawValues, rawValues.map((_, i) => i)));
  const internal = useRef(false);
  const onChange = (v: number[]) => { internal.current = true; onRawChange(v); };
  /** Re-sort the legend once a change is finished. The ring is left alone. */
  const settle = (raw = rawValues) => {
    if (raw.reduce((a, x) => a + x, 0) !== 100) return;
    setOrder((o) => byValue(raw, o));
  };
  // Changes from outside (presets, switching profile) settle straight away.
  useEffect(() => {
    if (internal.current) { internal.current = false; return; }
    settle();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rawValues.join('/')]);

  const svgRef = useRef<SVGSVGElement>(null);
  const [start, setStart] = useState(0); // rotation in points (0–100), so every divider can move
  const [active, setActive] = useState<number | null>(null); // highlighted segment
  const [drag, setDrag] = useState<number | null>(null); // divider being dragged
  const [lastEdited, setLastEdited] = useState<string | null>(null);
  const n = segs.length;
  const total = values.reduce((a, x) => a + x, 0);
  const balanced = total === 100;
  const cum = values.reduce<number[]>((acc, v) => [...acc, acc[acc.length - 1] + v], [0]); // c0..cn

  /** Divider k sits between segment k-1 and k (divider 0: between the last and the first). */
  const moveDivider = (k: number, rel: number) => {
    const v = [...values];
    if (k === 0) {
      let d = rel > 50 ? rel - 100 : rel;
      d = clamp(snap(d), -v[n - 1], v[0]);
      if (!d) return;
      v[0] -= d; v[n - 1] += d;
      setStart((s) => (s + d + 100) % 100);
    } else {
      const c = clamp(snap(rel), cum[k - 1], cum[k + 1]);
      if (c === cum[k]) return;
      v[k - 1] = c - cum[k - 1];
      v[k] = cum[k + 1] - c;
    }
    onChange(v);
  };

  const pointsAt = (e: PointerEvent) => {
    const r = svgRef.current!.getBoundingClientRect();
    const dx = e.clientX - (r.left + r.width / 2), dy = e.clientY - (r.top + r.height / 2);
    const deg = (Math.atan2(dx, -dy) * 180) / Math.PI;
    const p = ((deg + 360) % 360) / 3.6;
    return (p - start + 100) % 100;
  };

  const onKey = (k: number, e: KeyboardEvent) => {
    const step = e.key === 'ArrowRight' || e.key === 'ArrowUp' ? SNAP : e.key === 'ArrowLeft' || e.key === 'ArrowDown' ? -SNAP : 0;
    if (!step) return;
    e.preventDefault();
    moveDivider(k, k === 0 ? (step + 100) % 100 : cum[k] + step);
  };

  const setOne = (i: number, raw: string) => {
    const v = [...values];
    v[i] = clamp(Math.round(Number(raw.replace(/[^\d]/g, '')) || 0), 0, 100);
    setLastEdited(segs[i].key);
    onChange(v);
  };

  /** Scale the other segments so the total is 100, keeping the one just typed. */
  const balance = () => {
    const keep = lastEdited ? segs.findIndex((x) => x.key === lastEdited) : -1;
    const fixed = keep >= 0 ? values[keep] : 0;
    const others = values.map((x, i) => (i === keep ? 0 : x));
    const sum = others.reduce((a, x) => a + x, 0);
    const room = 100 - fixed;
    let v = values.map((x, i) => (i === keep ? x : sum ? Math.floor((x / sum) * room) : Math.floor(room / (n - (keep >= 0 ? 1 : 0)))));
    const diff = 100 - v.reduce((a, x) => a + x, 0);
    const top = v.map((x, i) => [x, i]).filter(([, i]) => i !== keep).sort((a, b) => b[0] - a[0])[0][1];
    v = v.map((x, i) => (i === top ? x + diff : x));
    onChange(v);
    settle(v);
  };

  // Draw with the real proportions even when the total isn't 100.
  const scale = total ? 100 / total : 0;
  const angles = cum.map((c) => (start + c * scale) * 3.6);
  const shown = active ?? drag;

  return (
    <div className={`donut ${drag != null ? 'dragging' : ''}`}>
      <div className="donut-chart">
        <svg ref={svgRef} className="donut-svg" viewBox="0 0 240 240" role="img" aria-label={`Priorities: ${segs.map((s, i) => `${s.label} ${values[i]}`).join(', ')}`}
          onPointerMove={(e) => { if (drag != null) moveDivider(drag, pointsAt(e)); }}
          onPointerUp={() => { if (drag != null) { setDrag(null); settle(); } }} onPointerCancel={() => { setDrag(null); settle(); }}>
          <circle cx={CX} cy={CY} r={R_MID} fill="none" stroke="var(--surface-3)" strokeWidth={R_OUT - R_IN} />
          {segs.map((s, i) => values[i] > 0 && (
            <path key={s.key} d={values[i] * scale >= 99.99 ? sector(0, 360) : sector(angles[i], angles[i + 1])}
              fill={s.color} className={`donut-seg ${shown != null && shown !== i ? 'dim' : ''}`}
              onPointerEnter={() => drag == null && setActive(i)} onPointerLeave={() => setActive(null)} />
          ))}
          {/* labels on segments big enough to hold them */}
          {segs.map((s, i) => {
            const share = values[i] * scale;
            if (share < 8) return null;
            const [x, y] = pt((angles[i] + angles[i + 1]) / 2, R_MID);
            return <text key={s.key} x={x} y={y} className="donut-pct" textAnchor="middle" dominantBaseline="central">{values[i]}</text>;
          })}
          {balanced && cum.slice(0, n).map((c, k) => {
            const [x, y] = pt((start + c) * 3.6, R_MID);
            const before = segs[(k - 1 + n) % n], after = segs[k];
            return (
              <g key={k} className={`donut-handle ${drag === k ? 'on' : ''}`} transform={`translate(${x} ${y})`}
                role="slider" tabIndex={0} aria-label={`Divider between ${before.label} and ${after.label}`}
                aria-valuemin={0} aria-valuemax={100} aria-valuenow={values[(k - 1 + n) % n]}
                aria-valuetext={`${before.label} ${values[(k - 1 + n) % n]}, ${after.label} ${values[k]}`}
                onKeyDown={(e) => onKey(k, e)} onBlur={() => settle()}
                onPointerDown={(e) => { e.preventDefault(); (e.currentTarget.ownerSVGElement as SVGSVGElement).setPointerCapture(e.pointerId); setDrag(k); setActive(null); }}>
                <circle r="16" fill="transparent" />
                <circle r="9" className="knob" />
              </g>
            );
          })}
        </svg>
        <div className="donut-center" aria-hidden>
          {!balanced ? (
            <><b className="warn-text">{total}</b><span>of 100</span></>
          ) : shown != null ? (
            <><b style={{ color: segs[shown].color }}>{values[shown]}</b><span>{segs[shown].label}</span></>
          ) : (
            <><b>100</b><span>points</span></>
          )}
        </div>
      </div>

      <ul className="donut-legend">
        {order.map((i) => segs[i]).map((s) => {
          const i = segs.indexOf(s);
          return (
          <li key={s.key} className={shown === i ? 'on' : ''} onPointerEnter={() => setActive(i)} onPointerLeave={() => setActive(null)}>
            <span className="swatch" style={{ background: s.color }} aria-hidden />
            <span className="lg-text">
              <label htmlFor={`w-${s.key}`}>{s.label}</label>
              <span>{s.help}</span>
            </span>
            <span className="lg-input">
              <input id={`w-${s.key}`} inputMode="numeric" value={values[i]} onFocus={(e) => e.target.select()}
                onChange={(e) => setOne(i, e.target.value)} onBlur={() => settle()} aria-describedby="donut-hint" />
            </span>
          </li>
          );
        })}
      </ul>

      <p id="donut-hint" className={`donut-hint ${balanced ? '' : 'warn'}`} role="status">
        {balanced
          ? <>Drag the white dividers (5-point steps), or type an exact number.</>
          : <>The points add up to {total}, not 100.{' '}
              <button type="button" className="link-btn" onClick={balance}><Scale size={14} aria-hidden /> Balance the others</button></>}
      </p>
    </div>
  );
}
