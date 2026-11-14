// TEMPORARY: a palette picker for choosing the brand colour on the real screens. Each palette sets the seven
// accent tokens (buttons, highlights, selection, links) for the light and the dark theme; the choice is kept in
// this browser only. Once the team has chosen, the palette goes into app.css and this file is deleted.
import { useEffect, useState } from 'react';
import { Check, Palette, X } from 'lucide-react';
import { useSession } from '../state/session';

interface Tokens { accent: string; ink: string; strong: string; top: string; bottom: string; soft: string; shadow: string }
interface Pal { id: string; name: string; note: string; light: Tokens; dark: Tokens }

const t = (accent: string, ink: string, strong: string, top: string, bottom: string, rgb: string, shadowRgb: string): Tokens =>
  ({ accent, ink, strong, top, bottom, soft: `rgba(${rgb}, 0.13)`, shadow: `0 8px 20px -10px rgba(${shadowRgb}, 0.55)` });

export const PALETTES: Pal[] = [
  { id: 'teal', name: 'Teal (current)', note: 'Fresh and calm; the colour we use now.',
    light: t('#17b6c4', '#0a7a86', '#0e98a6', '#2fcad6', '#13a9b7', '23, 182, 196', '14, 140, 152'),
    dark: t('#4cd0de', '#8be3ee', '#1497a5', '#2cbfcc', '#13919f', '76, 208, 222', '10, 30, 70') },
  { id: 'valio', name: 'Valio blue', note: 'Close to Valio’s own blue: reads as the supplier’s channel.',
    light: t('#1f6fd6', '#0d4fa6', '#1560c4', '#3c88e8', '#1559bd', '31, 111, 214', '21, 89, 189'),
    dark: t('#6aa8ff', '#a8cbff', '#2f74db', '#4a8ef0', '#2563c9', '106, 168, 255', '10, 30, 80') },
  { id: 'navygold', name: 'Navy & gold', note: 'The PasStar logo’s colours: navy buttons, gold highlights.',
    light: t('#1d3c94', '#152d72', '#1d3c94', '#2f52b5', '#18317c', '29, 60, 148', '24, 49, 124'),
    dark: t('#f2b632', '#ffd77a', '#2f52b5', '#3a5fc4', '#203e93', '242, 182, 50', '10, 20, 60') },
  { id: 'orange', name: 'Aimo orange', note: 'Warm and food-like; matches the A.I.mo mascot.',
    light: t('#e2611f', '#a9440f', '#d4571a', '#f07a3a', '#d25618', '226, 97, 31', '210, 86, 24'),
    dark: t('#ff8a4c', '#ffb58c', '#de5f1e', '#f17a3a', '#d0561a', '255, 138, 76', '60, 20, 0') },
  { id: 'forest', name: 'Forest green', note: 'Natural and trustworthy. Close to the “Reliable” green, so less contrast with it.',
    light: t('#1f8a5b', '#146b45', '#1a7a50', '#2fa56e', '#18784c', '31, 138, 91', '24, 120, 76'),
    dark: t('#4fd394', '#8fe6bb', '#1f9a63', '#2fb378', '#1a8a57', '79, 211, 148', '0, 40, 20') },
  { id: 'berry', name: 'Berry', note: 'Bold and memorable; stands apart from every status colour.',
    light: t('#c2185b', '#8e1043', '#b0154f', '#d63a76', '#a8124f', '194, 24, 91', '168, 18, 79'),
    dark: t('#ff6fa5', '#ffa8c8', '#cf2f6b', '#e04a83', '#b81f5a', '255, 111, 165', '60, 0, 20') },
  { id: 'graphite', name: 'Graphite', note: 'Quiet and premium; colour comes only from the status lights.',
    light: t('#3d4a5c', '#2a3442', '#344052', '#55657b', '#2f3a4a', '61, 74, 92', '40, 50, 64'),
    dark: t('#b6c3d6', '#dbe4f0', '#4b5a70', '#5d6e86', '#3f4d62', '182, 195, 214', '0, 0, 0') },
];

const KEY = 'passtar.palette';
const VARS: [keyof Tokens | 'grad', string][] = [['accent', '--accent'], ['ink', '--accent-ink'], ['strong', '--accent-strong'],
  ['grad', '--accent-grad'], ['soft', '--accent-soft'], ['shadow', '--accent-shadow'], ['strong', '--navy']];

function apply(p: Pal | undefined, theme: string) {
  const root = document.documentElement.style;
  for (const [, v] of VARS) root.removeProperty(v);
  if (!p || p.id === 'teal') return;           // the stylesheet already holds the current palette
  const k = theme === 'dark' ? p.dark : p.light;
  for (const [field, v] of VARS) {
    root.setProperty(v, field === 'grad' ? `linear-gradient(180deg, ${k.top} 0%, ${k.bottom} 100%)` : k[field]);
  }
}

export function ThemeLab() {
  const { theme, toggleTheme } = useSession();
  const [open, setOpen] = useState(false);
  const [id, setId] = useState(() => { try { return localStorage.getItem(KEY) ?? 'teal'; } catch { return 'teal'; } });
  const current = PALETTES.find((p) => p.id === id) ?? PALETTES[0];

  useEffect(() => {
    apply(current, theme);
    try { localStorage.setItem(KEY, current.id); } catch { /* private mode */ }
  }, [current, theme]);

  const k = theme === 'dark' ? current.dark : current.light;
  return (
    <div className="theme-lab">
      {open && (
        <section className="theme-lab-panel card" aria-label="Choose the brand colour">
          <header className="row" style={{ gap: 8 }}>
            <b className="grow">Brand colour</b>
            <button type="button" className="btn ghost sm" onClick={toggleTheme}>{theme === 'dark' ? 'See light' : 'See dark'}</button>
            <button type="button" className="btn ghost sm icon-btn" aria-label="Close" onClick={() => setOpen(false)}><X size={15} /></button>
          </header>
          <p className="small muted">Pick one: the whole app changes at once. Try a few pages and both themes, then tell us the name.</p>
          <div className="theme-lab-list" role="radiogroup" aria-label="Palettes">
            {PALETTES.map((p) => {
              const s = theme === 'dark' ? p.dark : p.light;
              return (
                <button key={p.id} type="button" role="radio" aria-checked={p.id === id} className={`theme-lab-item ${p.id === id ? 'on' : ''}`} onClick={() => setId(p.id)}>
                  <span className="theme-lab-swatches" aria-hidden>
                    <i style={{ background: `linear-gradient(180deg, ${s.top}, ${s.bottom})` }} />
                    <i style={{ background: s.accent }} />
                    <i style={{ background: s.soft, borderColor: s.accent }} />
                  </span>
                  <span className="grow">
                    <b>{p.name}</b>
                    <span className="small muted">{p.note}</span>
                  </span>
                  {p.id === id && <Check size={16} aria-hidden />}
                </button>
              );
            })}
          </div>
          <div className="theme-lab-codes mono">
            {theme === 'dark' ? 'dark' : 'light'} · button {k.top}→{k.bottom} · accent {k.accent} · text {k.ink}
          </div>
        </section>
      )}
      <button type="button" className="theme-lab-fab" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label="Choose the brand colour"
        title="Choose the brand colour">
        <Palette size={20} />
      </button>
    </div>
  );
}
