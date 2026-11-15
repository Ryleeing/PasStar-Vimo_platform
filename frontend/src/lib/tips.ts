// "Good to know" tips in plain words. The catalogue writes them for machines ("463047: Recorded label codes:
// GOODS_FROM_FINLAND_BLUE_SWAN", "deposit charge 120 cents"); people get a heading and a sentence instead.
// backend/app/orchestrator/agent_api.py does the same for agents, so both read alike.

export const TIP_TITLE: Record<string, string> = {
  STORAGE: 'Storage',
  CARBON: 'Carbon footprint',
  LABEL: 'Certifications',
  DEPOSIT: 'Deposit',
  ROUNDING: 'Rounded up',
  COLD_STORAGE_SPACE: 'Cold-storage space',
  RELIABILITY: 'Delivery record',
  QUOTE_TIMER: 'Price lock',
};

/** The certification codes in Valio's catalogue, by the names people know. */
export const LABEL_NAME: Record<string, string> = {
  GOODS_FROM_FINLAND_BLUE_SWAN: 'Made in Finland (Blue Swan)',
  MADE_IN_FINLAND_FLAG_WITH_KEY: 'Made in Finland (Key Flag)',
  FINNISH_HEART_SYMBOL: 'Heart Symbol (a better nutritional choice)',
  EU_ORGANIC_FARMING: 'EU organic',
  SVANEN: 'Nordic Swan Ecolabel',
  RAINFOREST_ALLIANCE: 'Rainforest Alliance',
  EU_ECO_LABEL: 'EU Ecolabel',
  MARINE_STEWARDSHIP_COUNCIL_LABEL: 'MSC sustainable seafood',
  FAIR_TRADE_MARK: 'Fairtrade',
  AQUACULTURE_STEWARDSHIP_COUNCIL: 'ASC responsibly farmed',
  KRAV_MARK: 'KRAV organic',
  OFFICIAL_ECO_LABEL_SUN: 'Finnish organic (Sun label)',
  SOIL_ASSOCIATION_ORGANIC_SYMBOL: 'Soil Association organic',
  PUHTAASTI_KOTIMAINEN: 'Purely Finnish',
  DOLPHIN_SAFE: 'Dolphin safe',
  UTZ_CERTIFIED: 'UTZ certified',
};

const words = (code: string) => code.toLowerCase().replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
const euro = (cents: number) => `€${(cents / 100).toFixed(2)}`;

/** Heading and plain sentence for one tip. Unknown wording is kept as written, minus the product-number prefix. */
export function tipView(kind: string, raw: string): { title: string; text: string } {
  const text = raw.replace(/^\s*\d+:\s*/, '').trim();
  const title = TIP_TITLE[kind] ?? words(kind);
  let m: RegExpMatchArray | null;
  switch (kind) {
    case 'STORAGE':
      return { title, text: text.replace(/^Store /, 'Keep ') };
    case 'CARBON':
      if ((m = text.match(/footprint factor:\s*([\d.]+)/i))) {
        return { title, text: `About ${m[1]} kg CO₂e per kg of product. That is typical for this kind of product, not this order’s exact total.` };
      }
      break;
    case 'LABEL':
      if ((m = text.match(/label codes:\s*(.+)$/i))) {
        const names = m[1].split(/\s*,\s*/).filter(Boolean).map((c) => LABEL_NAME[c] ?? words(c));
        return { title, text: `${names.join(' · ')}.` };
      }
      break;
    case 'DEPOSIT':
      if ((m = text.match(/deposit charge\s*(\d+)\s*cents/i))) return { title, text: `Includes ${euro(Number(m[1]))} for returnable packaging.` };
      if (/confirm its charge/i.test(text)) return { title, text: 'Comes in returnable packaging; the deposit amount is not confirmed yet.' };
      break;
    case 'ROUNDING':
      if ((m = text.match(/extra quantity\s*([\d.]+\s*\S+?)\.?$/i))) return { title, text: `Rounded up to full packs, so you get ${m[1]} extra.` };
      break;
    case 'COLD_STORAGE_SPACE':
      if ((m = text.match(/volume\s*([\d.]+)\s*L per case/i))) return { title, text: `Each case takes about ${m[1]} L of space in your cold room.` };
      break;
    case 'RELIABILITY':
      if ((m = text.match(/reliability:\s*(\w+)/i))) return { title, text: `Delivery record: ${m[1].toLowerCase()}.` };
      break;
  }
  return { title, text };
}
