/** Number and date formatting, in one place so the screens agree. */

/** Indian digit grouping: 1,00,000 rather than 100,000. */
const RUPEES = new Intl.NumberFormat('en-IN', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function money(value: number | null | undefined): string {
  return value == null ? '—' : RUPEES.format(value);
}

export function percent(value: number | null | undefined, digits = 0): string {
  if (value == null) return '—';
  // Signed on purpose: "+18%" and "18%" read the same at a glance, and the
  // sign is the whole meaning for a discount or a headroom figure.
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`;
}

export function compact(value: number | null | undefined): string {
  if (value == null) return '—';
  const abs = Math.abs(value);
  if (abs >= 1e5) return `${(value / 1e5).toFixed(2)}L`;
  if (abs >= 1e3) return `${(value / 1e3).toFixed(1)}k`;
  return value.toFixed(0);
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** `2026-09-04` as `4 Sep 2026`; the input unchanged if it is not a date. */
export function humanDate(stamp: string | null | undefined): string | null {
  if (!stamp) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(stamp);
  if (!match) return null;
  const [, year, month, day] = match;
  const name = MONTHS[Number(month) - 1];
  return name ? `${Number(day)} ${name} ${year}` : null;
}

/**
 * How current the data is.
 *
 * Prices refresh nightly and fundamentals quarterly, so the two dates are
 * reported separately rather than collapsed into one "last updated" -- which
 * would be wrong for one of them, and misleading in the direction that
 * matters: a fresh price date must not imply fresh fundamentals.
 */
export function freshness(
  priced: string | null | undefined,
  scraped: string | null | undefined,
): string {
  const parts: string[] = [];
  const p = humanDate(priced);
  const s = humanDate(scraped);
  if (p) parts.push(`Prices as of ${p}`);
  if (s) parts.push(`fundamentals to ${s}`);
  if (!parts.length) return 'Screened from stored data.';
  return `${parts.join(' · ')} · prices refresh nightly`;
}
