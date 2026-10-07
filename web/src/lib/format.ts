/**
 * Number and date formatting, in one place so the screens agree.
 *
 * Ported from the phone app so a figure reads identically on both. The one
 * addition is `crore`, because the web screener shows market capitalisation and
 * the app does not.
 */

/** Indian digit grouping: 1,00,000 rather than 100,000. */
const RUPEES = new Intl.NumberFormat('en-IN', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const WHOLE = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });

export const DASH = '—';

export function money(value: number | null | undefined): string {
  return value == null ? DASH : RUPEES.format(value);
}

export function count(value: number | null | undefined): string {
  return value == null ? DASH : WHOLE.format(value);
}

export function percent(value: number | null | undefined, digits = 0): string {
  if (value == null) return DASH;
  // Signed on purpose: "+18%" and "18%" read the same at a glance, and the sign
  // is the whole meaning for a discount or a headroom figure.
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`;
}

export function plain(value: number | null | undefined, digits = 1): string {
  return value == null ? DASH : value.toFixed(digits);
}

/**
 * Market capitalisation, which the source gives in rupees crore.
 *
 * Indian convention all the way up, because a reader here thinks in crore and
 * lakh crore rather than billions: 1,78,574 crore is written ₹1.79L Cr.
 */
export function crore(value: number | null | undefined): string {
  if (value == null) return DASH;
  if (value >= 1e5) return `₹${(value / 1e5).toFixed(2)}L Cr`;
  if (value >= 1e3) return `₹${WHOLE.format(Math.round(value))} Cr`;
  return `₹${value.toFixed(0)} Cr`;
}

/** The cap bands an Indian reader filters by, in rupees crore. */
export const CAP_BANDS = [
  { label: 'Large', min: 20000, max: Infinity },
  { label: 'Mid', min: 5000, max: 20000 },
  { label: 'Small', min: 500, max: 5000 },
  { label: 'Micro', min: 0, max: 500 },
] as const;

export type CapBand = (typeof CAP_BANDS)[number]['label'];

export function capBand(marketCap: number | null | undefined): CapBand | null {
  if (marketCap == null) return null;
  const band = CAP_BANDS.find((b) => marketCap >= b.min && marketCap < b.max);
  return band ? band.label : null;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** `2026-09-04` as `4 Sep 2026`; null if it is not a date. */
export function humanDate(stamp: string | null | undefined): string | null {
  if (!stamp) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(stamp);
  if (!match) return null;
  const [, year, month, day] = match;
  const name = MONTHS[Number(month) - 1];
  return name ? `${Number(day)} ${name} ${year}` : null;
}
