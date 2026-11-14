// The order details every confirmation step shows (requester's review and approver's review alike),
// so nothing is ever ordered without the same full picture.
import type { ReactNode } from 'react';
import { AlertTriangle, Check, MapPin, Package, ShieldCheck, Truck } from 'lucide-react';
import type { Candidate, CandidateLine, Totals } from '../api/types';
import { ALLERGEN_STATUS, AVAILABILITY, dayLabel, eur, num } from '../lib/format';
import { Pill } from './ui';

export function OrderDetails({ line, candidate, deliveryDate, address }: {
  line: CandidateLine; candidate?: Candidate | null; deliveryDate: string; address?: string | null;
}) {
  const perUnit = line.sales_units ? line.qty_covered.value / line.sales_units : null;
  const av = AVAILABILITY[line.available_on_date];
  const rows = candidate?.cards.allergen.rows ?? [];
  const notes = candidate?.cards.allergen.notes ?? [];

  return (
    <div className="review-details">
      <section className="review-row">
        <Package size={20} className="review-icon" aria-hidden />
        <div className="stack sm grow">
          <h3>{line.name}</h3>
          <p className="text-2">
            {num(line.sales_units)} × {line.sales_unit}{perUnit ? `, ${num(perUnit)} ${line.qty_covered.unit} each` : ''} = <b>{num(line.qty_covered.value)} {line.qty_covered.unit}</b>
          </p>
          {line.overbuy && <p className="small muted">{num(line.overbuy.value)} {line.overbuy.unit} more than asked, because of pack sizes</p>}
        </div>
      </section>

      <section className="review-row">
        <Truck size={20} className="review-icon" aria-hidden />
        <div className="stack sm grow">
          <div className="row between wrap">
            <h3>Delivery by {dayLabel(deliveryDate)}</h3>
            {line.available_on_date !== 'YES' && <Pill tone={av.tone}>{av.label}</Pill>}
          </div>
          {address && <p className="text-2 row" style={{ gap: 6 }}><MapPin size={15} className="muted" />{address}</p>}
        </div>
      </section>

      {rows.length > 0 && (
        <section className="review-row">
          <ShieldCheck size={20} className="review-icon" aria-hidden />
          <div className="stack sm grow">
            <h3>Requirements</h3>
            {rows.map((r, i) => {
              const st = ALLERGEN_STATUS[r.status];
              return (
                <div key={i} className="row wrap" style={{ gap: 8 }}>
                  {st.tone === 'ok' ? <Check size={16} color="var(--ok)" /> : <AlertTriangle size={16} color="var(--warn)" />}
                  <span className="grow nowrap">{r.requirement.label ?? r.requirement.code}</span>
                  <span className="small muted">{st.label} by the manufacturer</span>
                </div>
              );
            })}
            {notes.map((n) => <p key={n} className="small warn-text">{n}</p>)}
          </div>
        </section>
      )}
    </div>
  );
}

/** Right-hand summary: price breakdown, an optional note, then exactly one committing action. */
export function OrderSummary({ totals, note, action, footer }: { totals: Totals; note?: ReactNode; action: ReactNode; footer?: ReactNode }) {
  return (
    <aside className="review-summary" aria-label="Order summary">
      <h3>Summary</h3>
      <dl className="sum">
        <div><dt>Net</dt><dd>{eur(totals.net_cents)}</dd></div>
        <div><dt>VAT</dt><dd>{eur(totals.vat_cents)}</dd></div>
        {totals.deposit_cents > 0 && <div><dt>Deposits</dt><dd>{eur(totals.deposit_cents)}</dd></div>}
        <div className="sum-total"><dt>Total</dt><dd>{eur(totals.gross_cents)}</dd></div>
      </dl>
      {note && <p className="small text-2">{note}</p>}
      {action}
      {footer}
    </aside>
  );
}
