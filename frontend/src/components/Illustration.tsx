// Flat, minimal illustration for the order confirmation: a parcel on its way with a check badge.
// Inline SVG so it inherits theme tokens (works in dark and light) and needs no asset files.
export function OrderedIllustration() {
  return (
    <svg className="illo" viewBox="0 0 280 220" role="img" aria-label="A parcel on its way">
      {/* backdrop */}
      <circle cx="140" cy="108" r="90" fill="var(--accent-soft)" />
      <ellipse cx="140" cy="198" rx="78" ry="8" fill="var(--illo-shadow)" />

      {/* motion lines: on its way */}
      <g stroke="var(--border-strong)" strokeWidth="5" strokeLinecap="round">
        <line x1="34" y1="132" x2="62" y2="132" />
        <line x1="22" y1="150" x2="62" y2="150" />
        <line x1="40" y1="168" x2="62" y2="168" />
      </g>

      {/* parcel */}
      <g className="illo-box">
        <polygon points="80,104 140,80 200,104 140,128" fill="#F6C38F" />
        <polygon points="80,104 140,128 140,192 80,168" fill="#EBA066" />
        <polygon points="140,128 200,104 200,168 140,192" fill="#D9843F" />
        {/* tape */}
        <polygon points="104,94 116,89 176,113 164,118" fill="#FBE1C2" />
        <polygon points="164,118 176,113 176,131 164,136" fill="#F3CB9C" />
        {/* label */}
        <polygon points="92,134 124,147 124,166 92,153" fill="#FFF8F0" />
        <line x1="97" y1="142" x2="118" y2="150.5" stroke="#E2B48A" strokeWidth="3" strokeLinecap="round" />
        <line x1="97" y1="150" x2="111" y2="155.7" stroke="#E2B48A" strokeWidth="3" strokeLinecap="round" />
      </g>

      {/* check badge */}
      <g className="illo-badge">
        <circle cx="204" cy="72" r="26" fill="var(--ok)" />
        <path d="M192 72.5 l8 8 l16 -17" fill="none" stroke="#fff" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round" />
      </g>

      {/* sparkles */}
      <g fill="var(--accent)">
        <path d="M66 62 l3 9 l9 3 l-9 3 l-3 9 l-3 -9 l-9 -3 l9 -3 z" />
        <path d="M232 132 l2 6 l6 2 l-6 2 l-2 6 l-2 -6 l-6 -2 l6 -2 z" opacity=".7" />
        <circle cx="96" cy="40" r="3.5" opacity=".6" />
        <circle cx="244" cy="98" r="3" opacity=".5" />
      </g>
    </svg>
  );
}
