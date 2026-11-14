// Turns a sentence like "No Arla, everything nut-free, skip 464179" into buying-rule suggestions.
// There is no backend endpoint for this yet, so it runs in the browser and only uses vocabulary the
// catalogue already defines (facets, brands seen in searches, product numbers). Every suggestion is
// shown to the person to confirm before it is added. Swap in a backend parser when one exists.
import type { Facets, Requirement } from '../api/types';

export type RuleSuggestion =
  | { kind: 'brand'; value: string; label: string; isNew: boolean }
  | { kind: 'product'; value: string; label: string }
  | { kind: 'requirement'; value: Requirement; label: string };

export interface Interpretation {
  rules: RuleSuggestion[];
  /** Words that sound like a ranking priority rather than a rule (cheap, local, …). */
  priorityHints: string[];
}

const COMMON_BRANDS = ['Valio', 'Arla', 'Tila', 'Oatly', 'Alpro', 'Fazer', 'Juustoportti', 'Pirkka', 'Kotimaista'];

/** Requirement patterns → catalogue codes. Order matters: "peanut" before "nut". */
const REQ_PATTERNS: { re: RegExp; kind: Requirement['kind']; code: string; group: 'claims' | 'allergens' | 'labels' }[] = [
  { re: /\bpeanut/i, kind: 'ALLERGEN_FREE', code: 'AP', group: 'allergens' },
  { re: /(?<!pea)\bnuts?\b|\bnut[- ]free|\btree[- ]nut/i, kind: 'ALLERGEN_FREE', code: 'AN', group: 'allergens' },
  { re: /\beggs?\b|\begg[- ]free/i, kind: 'ALLERGEN_FREE', code: 'AE', group: 'allergens' },
  { re: /\bdairy|\bmilk[- ]free|\bno milk\b|\bwithout milk\b/i, kind: 'ALLERGEN_FREE', code: 'AM', group: 'allergens' },
  { re: /\blactose/i, kind: 'CLAIM', code: 'FREE_FROM_LACTOSE', group: 'claims' },
  { re: /\bgluten/i, kind: 'CLAIM', code: 'FREE_FROM_GLUTEN', group: 'claims' },
  { re: /\bvegan/i, kind: 'CLAIM', code: 'VEGAN', group: 'claims' },
  { re: /\blow[- ]fat/i, kind: 'CLAIM', code: 'LOW_FAT', group: 'claims' },
  { re: /\borganic/i, kind: 'LABEL', code: 'EU_ORGANIC_FARMING', group: 'labels' },
  { re: /\bblue swan|\bmade in finland|\bfinnish[- ]made/i, kind: 'LABEL', code: 'GOODS_FROM_FINLAND_BLUE_SWAN', group: 'labels' },
];

const PRIORITY_WORDS: [RegExp, string][] = [
  [/\bcheap|\bprice|\bcost|\bbudget/i, 'price'],
  [/\blocal\b|\bnearby/i, 'local origin'],
  [/\bcarbon|\bco2|\bgreen(er)?\b|\bclimate/i, 'low carbon'],
  [/\breliab|\bon[- ]time|\bfast(er)? delivery/i, 'on-time delivery'],
];

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

export function interpretRules(text: string, ctx: { facets: Facets | null; knownBrands: string[]; knownProducts: Map<string, string> }): Interpretation {
  const rules: RuleSuggestion[] = [];
  const t = text.trim();
  if (!t) return { rules, priorityHints: [] };

  // Brands we know of, anywhere in the sentence.
  const brands = [...new Set([...ctx.knownBrands, ...COMMON_BRANDS])];
  for (const b of brands) {
    if (new RegExp(`\\b${escape(b)}\\b`, 'i').test(t)) rules.push({ kind: 'brand', value: b, label: b, isNew: !ctx.knownBrands.includes(b) });
  }
  // A capitalised word right after "no / not / avoid / without / except / never …" may be a brand we haven't seen.
  for (const m of t.matchAll(/\b(?:no|not|avoid|without|exclude|except|never|skip)\s+(?:any\s+|the\s+)?([A-ZÅÄÖ][\wåäöÅÄÖ-]{1,})/g)) {
    const w = m[1];
    const isRequirementWord = REQ_PATTERNS.some((p) => p.re.test(w));
    if (!isRequirementWord && !rules.some((r) => r.kind === 'brand' && r.value.toLowerCase() === w.toLowerCase())) {
      rules.push({ kind: 'brand', value: w, label: w, isNew: true });
    }
  }

  // Product numbers (Valio uses 6 digits).
  for (const m of t.matchAll(/\b(\d{6})\b/g)) {
    rules.push({ kind: 'product', value: m[1], label: ctx.knownProducts.get(m[1]) ?? `Product ${m[1]}` });
  }
  // Product names we have seen, if mentioned by name.
  for (const [id, name] of ctx.knownProducts) {
    const short = name.split(/\s+\d/)[0]; // "Farm milk 1 l lactose free" → "Farm milk"
    if (short.length > 4 && new RegExp(`\\b${escape(short)}\\b`, 'i').test(t) && !rules.some((r) => r.kind === 'product' && r.value === id)) {
      rules.push({ kind: 'product', value: id, label: name });
    }
  }

  // Requirements every product must meet.
  for (const p of REQ_PATTERNS) {
    if (!p.re.test(t)) continue;
    const facet = ctx.facets?.[p.group]?.find((f) => f.code === p.code);
    const label = p.kind === 'ALLERGEN_FREE' ? `Free from ${(facet?.label ?? p.code).toLowerCase()}` : facet?.label ?? p.code;
    rules.push({ kind: 'requirement', value: { kind: p.kind, code: p.code, label: facet?.label ?? null }, label });
  }

  const priorityHints = PRIORITY_WORDS.filter(([re]) => re.test(t)).map(([, name]) => name);
  return { rules, priorityHints };
}
