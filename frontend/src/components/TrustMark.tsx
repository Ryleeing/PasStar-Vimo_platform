// Trust in traffic-light language, the same everywhere: a green tick "Reliable", a yellow question mark
// "Reliable?", a red cross "Risky". The score (0-100) comes from the backend and always sits inside its level's
// band (Reliable 80-100, Reliable? 50-79, Risky 0-49), so the word and the number never disagree.
import { Check, X } from 'lucide-react';

type Level = 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN' | string;

export const VERDICT: Record<string, { label: string; light: 'go' | 'caution' | 'stop' }> = {
  HIGH: { label: 'Reliable', light: 'go' },
  MEDIUM: { label: 'Reliable?', light: 'caution' },
  LOW: { label: 'Risky', light: 'stop' },
  UNKNOWN: { label: 'Risky', light: 'stop' },
};
export const verdictOf = (level?: Level | null) => VERDICT[level ?? 'UNKNOWN'] ?? VERDICT.UNKNOWN;

/** The traffic-light symbol alone. */
export function TrustLight({ level, size = 18 }: { level?: Level | null; size?: number }) {
  const v = verdictOf(level);
  const glyph = Math.round(size * 0.62);
  return (
    <span className={`tl-dot tl-${v.light}`} style={{ width: size, height: size }} aria-hidden>
      {v.light === 'go' ? <Check size={glyph} strokeWidth={3.2} /> : v.light === 'stop' ? <X size={glyph} strokeWidth={3.2} /> : <b style={{ fontSize: glyph + 1 }}>?</b>}
    </span>
  );
}

/** Symbol + word + score, e.g. [✓] Reliable 86. */
export function TrustMark({ level, score, size = 18, label }: { level?: Level | null; score?: number | null; size?: number; label?: string }) {
  const v = verdictOf(level);
  return (
    <span className={`tl tl-chip tl-chip-${v.light}`} title={score != null ? `${v.label}: ${score} out of 100` : v.label}>
      <TrustLight level={level} size={size} />
      {label && <span className="tl-label">{label}</span>}
      <b>{v.label}</b>
      {score != null && <span className="tl-score num">{score}<span className="tl-of">/100</span></span>}
    </span>
  );
}

/** A score bar in the same traffic-light colour, for the four dimensions. */
export function ScoreBar({ level, score }: { level?: Level | null; score?: number | null }) {
  const v = verdictOf(level);
  return (
    <span className="tl-bar" aria-hidden>
      <i className={`tl-fill tl-${v.light}`} style={{ width: `${Math.max(4, Math.min(100, score ?? 0))}%` }} />
    </span>
  );
}
