// A small Markdown renderer for agent replies: headings, paragraphs, bullet and numbered lists, bold, italics and
// inline code. It builds React elements and never injects HTML: the text comes from a model that has read product
// names, so it is untrusted.
import { Fragment, type ReactNode } from 'react';

const INLINE = /(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g;

function inline(text: string, key: string): ReactNode[] {
  return text.split(INLINE).filter(Boolean).map((part, i) => {
    const k = `${key}-${i}`;
    if (/^(\*\*|__).+\1$/.test(part)) return <strong key={k}>{part.slice(2, -2)}</strong>;
    if (/^`.+`$/.test(part)) return <code key={k} className="mono">{part.slice(1, -1)}</code>;
    if (/^([*_]).+\1$/.test(part)) return <em key={k}>{part.slice(1, -1)}</em>;
    return <Fragment key={k}>{part}</Fragment>;
  });
}

type Block =
  | { t: 'h'; level: number; text: string }
  | { t: 'p'; lines: string[] }
  | { t: 'ul' | 'ol'; items: string[] }
  | { t: 'hr' };

function blocks(src: string): Block[] {
  const out: Block[] = [];
  for (const raw of src.replace(/\r\n/g, '\n').split('\n')) {
    const line = raw.trimEnd();
    const last = out[out.length - 1];
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    const ul = line.match(/^\s*[-*•]\s+(.*)$/);
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (!line.trim()) {
      out.push({ t: 'p', lines: [] });            // paragraph break; empty ones are dropped below
    } else if (/^\s*(---|\*\*\*|___)\s*$/.test(line)) {
      out.push({ t: 'hr' });
    } else if (h) {
      out.push({ t: 'h', level: h[1].length, text: h[2] });
    } else if (ul) {
      if (last?.t === 'ul') last.items.push(ul[1]);
      else out.push({ t: 'ul', items: [ul[1]] });
    } else if (ol) {
      if (last?.t === 'ol') last.items.push(ol[1]);
      else out.push({ t: 'ol', items: [ol[1]] });
    } else if (last?.t === 'p') {
      last.lines.push(line);
    } else {
      out.push({ t: 'p', lines: [line] });
    }
  }
  return out.filter((b) => b.t !== 'p' || b.lines.length);
}

export function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      {blocks(text).map((b, i) => {
        const k = `b${i}`;
        switch (b.t) {
          case 'h': {
            const Tag = (`h${Math.min(6, b.level + 2)}`) as 'h3' | 'h4' | 'h5' | 'h6';
            return <Tag key={k}>{inline(b.text, k)}</Tag>;
          }
          case 'ul': return <ul key={k}>{b.items.map((it, j) => <li key={j}>{inline(it, `${k}-${j}`)}</li>)}</ul>;
          case 'ol': return <ol key={k}>{b.items.map((it, j) => <li key={j}>{inline(it, `${k}-${j}`)}</li>)}</ol>;
          case 'hr': return <hr key={k} />;
          default: return <p key={k}>{b.lines.map((l, j) => <Fragment key={j}>{j > 0 && <br />}{inline(l, `${k}-${j}`)}</Fragment>)}</p>;
        }
      })}
    </div>
  );
}
