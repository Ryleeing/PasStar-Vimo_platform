// Aimo — Valio Aimo's assistant robot, redrawn as a flat, simplified illustration for this prototype:
// white shell, dark-teal visor with LED-dot eyes, ear pods with a green light, teal scarf.
// If Valio provides official artwork, swap it in here; every surface uses these two components.
export type MascotMood = 'idle' | 'thinking' | 'asking' | 'happy';

/** LED face drawn with dotted strokes so it reads like a dot-matrix screen. */
function Face({ mood }: { mood: MascotMood }) {
  const led = { fill: 'none', stroke: 'var(--m-eye)', strokeWidth: 2.8, strokeLinecap: 'round' as const, strokeDasharray: '0.1 3.1' };
  return (
    <g className="m-face">
      <g className="m-eyes" {...led}>
        {mood === 'thinking' && (<><path d="M40 53 h11" /><path d="M69 53 h11" /></>)}
        {mood === 'asking' && (<><circle cx="45.5" cy="52.5" r="4.6" /><circle cx="74.5" cy="52.5" r="4.6" /></>)}
        {(mood === 'idle' || mood === 'happy') && (
          <><path d={mood === 'happy' ? 'M39 56 q6.5 -10 13 0' : 'M40 55 q5.5 -8 11 0'} /><path d={mood === 'happy' ? 'M68 56 q6.5 -10 13 0' : 'M69 55 q5.5 -8 11 0'} /></>
        )}
      </g>
      <g className="m-mouth" {...led}>
        {mood === 'thinking' && <path className="m-dots" d="M54 63 h12" />}
        {mood === 'asking' && <circle cx="60" cy="63" r="2.2" />}
        {mood === 'idle' && <path d="M53 61 q7 6 14 0" />}
        {mood === 'happy' && <path d="M51 60 q9 9 18 0" />}
      </g>
    </g>
  );
}

export function Mascot({ mood = 'idle', size = 120, title = 'Aimo, your ordering assistant' }: { mood?: MascotMood; size?: number; title?: string }) {
  return (
    <svg className={`mascot mood-${mood}`} width={size} height={size * 1.17} viewBox="0 0 120 140" role="img" aria-label={title}>
      <ellipse className="m-shadow" cx="60" cy="134" rx="24" ry="4.5" fill="var(--illo-shadow)" />
      <g className="m-body">
        {/* scarf tail, behind everything */}
        <path d="M44 94 Q26 104 24 126 Q40 122 52 104 Z" fill="var(--m-scarf-2)" />

        {/* legs */}
        <rect x="47" y="118" width="10" height="11" rx="5" fill="var(--m-shell)" stroke="var(--m-line)" strokeWidth="1.2" />
        <rect x="63" y="118" width="10" height="11" rx="5" fill="var(--m-shell)" stroke="var(--m-line)" strokeWidth="1.2" />

        {/* right arm, resting */}
        <path d="M78 100 q10 6 12 16" fill="none" stroke="var(--m-shell-2)" strokeWidth="7" strokeLinecap="round" />
        <circle cx="90" cy="117" r="5" fill="var(--m-shell)" stroke="var(--m-line)" strokeWidth="1.2" />

        {/* torso */}
        <rect x="40" y="88" width="40" height="34" rx="17" fill="var(--m-shell)" stroke="var(--m-line)" strokeWidth="1.2" />
        <path d="M68 89 a17 17 0 0 1 12 16 v1 a17 17 0 0 1 -12 16 Z" fill="var(--m-shade)" />
        <rect x="51" y="104" width="18" height="8" rx="4" fill="var(--m-shell-2)" />

        {/* left arm, waving */}
        <g className="m-arm">
          <path d="M43 98 q-12 -4 -16 -18" fill="none" stroke="var(--m-shell-2)" strokeWidth="7" strokeLinecap="round" />
          <circle cx="26" cy="78" r="5.5" fill="var(--m-shell)" stroke="var(--m-line)" strokeWidth="1.2" />
        </g>

        {/* scarf around the neck */}
        <path d="M38 86 Q60 98 82 86 L81 94 Q60 106 39 94 Z" fill="var(--m-scarf)" />

        {/* head */}
        <g className="m-head">
          <rect x="18" y="42" width="11" height="24" rx="5.5" fill="var(--m-shell-2)" stroke="var(--m-line)" strokeWidth="1.2" />
          <rect x="91" y="42" width="11" height="24" rx="5.5" fill="var(--m-shell-2)" stroke="var(--m-line)" strokeWidth="1.2" />
          <rect className="m-light" x="21.5" y="47" width="3" height="14" rx="1.5" fill="var(--m-light)" />
          <ellipse cx="60" cy="54" rx="36" ry="33" fill="var(--m-shell)" stroke="var(--m-line)" strokeWidth="1.2" />
          <path d="M78 25 a36 33 0 0 1 0 58 a40 40 0 0 0 0 -58 Z" fill="var(--m-shade)" />
          <ellipse cx="46" cy="30" rx="12" ry="5" fill="#fff" opacity=".7" />
          {/* visor */}
          <rect x="30" y="38" width="60" height="34" rx="17" fill="var(--m-visor)" />
          <path d="M38 43 q12 -4 26 -2" fill="none" stroke="#fff" strokeOpacity=".18" strokeWidth="3" strokeLinecap="round" />
          <Face mood={mood} />
        </g>
      </g>

      {mood === 'asking' && (
        <g className="m-bubble">
          <rect x="88" y="2" width="28" height="24" rx="12" fill="var(--accent)" />
          <path d="M94 24 l-4 7 l9 -5 Z" fill="var(--accent)" />
          <text x="102" y="20" textAnchor="middle" fontSize="17" fontWeight="800" fill="#fff" fontFamily="var(--font)">?</text>
        </g>
      )}
      {mood === 'happy' && (
        <g className="m-sparkles" fill="var(--accent)">
          <path d="M102 16 l2 6 l6 2 l-6 2 l-2 6 l-2 -6 l-6 -2 l6 -2 z" />
          <path d="M10 40 l1.5 4.5 l4.5 1.5 l-4.5 1.5 l-1.5 4.5 l-1.5 -4.5 l-4.5 -1.5 l4.5 -1.5 z" />
        </g>
      )}
    </svg>
  );
}

/** Just the visor, for avatars and the logo. */
export function MascotFace({ size = 32, mood = 'idle' }: { size?: number; mood?: MascotMood }) {
  return (
    <svg className={`mascot-face mood-${mood}`} width={size} height={size * (34 / 60)} viewBox="30 38 60 34" aria-hidden>
      <rect x="30" y="38" width="60" height="34" rx="17" fill="var(--m-visor)" />
      <Face mood={mood} />
    </svg>
  );
}
