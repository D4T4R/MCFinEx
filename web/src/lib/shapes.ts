/**
 * The arithmetic behind the cross-section charts.
 *
 * Kept out of the components because this is where the charts can be wrong in
 * a way nobody sees. A bar at the wrong height still looks like a bar, and a
 * median computed over the wrong subset still draws a line -- the failures
 * here are silent and plausible, which is exactly the kind worth testing.
 */

/** One bar of a histogram. */
export interface Bin {
  /** Inclusive lower edge, in the metric's own units. */
  from: number;
  /** Exclusive upper edge, except on the last bin where it is inclusive. */
  to: number;
  count: number;
}

export interface Distribution {
  bins: Bin[];
  median: number | null;
  /** 10th and 90th percentile, for context either side of the median. */
  p10: number | null;
  p90: number | null;
  /** Values that could not be placed: non-finite, or non-positive on a log axis. */
  excluded: number;
  /**
   * Plottable values lying outside the requested window. Counted apart from
   * `excluded` because these are real companies deliberately left off the end
   * of the axis, not companies with nothing to plot.
   */
  clipped: number;
}

function percentile(sorted: number[], p: number): number | null {
  if (!sorted.length) return null;
  const i = Math.min(sorted.length - 1, Math.max(0, Math.floor(sorted.length * p)));
  return sorted[i] ?? null;
}

/**
 * Bin `values` into a histogram, in log space when the axis is logarithmic.
 *
 * Binning in log space is the whole point on this data. P/E runs from under 1
 * to 27,294 against a median of 23.4; split into even linear bins, the first
 * bar holds 99% of the market and the rest of the chart is empty -- which
 * reads as a broken chart rather than as a skewed distribution. The scatter
 * learned this already and the histogram inherits the same `scale` setting, so
 * the two always describe the same axis the same way.
 *
 * Percentiles are taken over the values that were actually placed, not over
 * the input, so the median line always sits inside the bars it describes.
 *
 * `min`/`max` clamp the binned window, and exist because the scatter clamps its
 * axes the same way. Left unclamped, modelled upside runs from -280,358% to
 * 1,929,444%: twenty-eight linear bins over that span put all 2,523 companies
 * in one bar. Worse, the scatter beside it is drawn over the middle 96% of the
 * same metric, so the two charts would be showing the same number on two
 * different scales -- the one thing this pair exists to avoid.
 */
export function distribution(
  values: (number | null | undefined)[],
  { log = false, bins = 28, min, max }: {
    log?: boolean; bins?: number; min?: number; max?: number;
  } = {},
): Distribution {
  const plottable = values.filter(
    (v): v is number => typeof v === 'number' && Number.isFinite(v) && (!log || v > 0),
  );
  const excluded = values.length - plottable.length;

  const within = (v: number) => (min == null || v >= min) && (max == null || v <= max);
  const usable = plottable.filter(within);
  const clipped = plottable.length - usable.length;

  if (!usable.length) {
    return { bins: [], median: null, p10: null, p90: null, excluded, clipped };
  }

  // Percentiles over everything plottable rather than over the clamped window,
  // so the median line is the market's real median and not the median of the
  // middle 96% of it. The window is itself a percentile band, so the line still
  // lands inside the bars.
  const ranked = [...plottable].sort((a, b) => a - b);
  const sorted = [...usable].sort((a, b) => a - b);
  const lo = min ?? (sorted[0] as number);
  const hi = max ?? (sorted[sorted.length - 1] as number);

  const toSpace = (v: number) => (log ? Math.log10(v) : v);
  const fromSpace = (v: number) => (log ? 10 ** v : v);
  const spanLo = toSpace(lo);
  const spanHi = toSpace(hi);

  // Every value identical: one bin rather than a divide-by-zero that silently
  // produces NaN edges and an empty chart.
  if (spanHi === spanLo) {
    return {
      bins: [{ from: lo, to: hi, count: usable.length }],
      median: percentile(ranked, 0.5), p10: lo, p90: hi, excluded, clipped,
    };
  }

  const width = (spanHi - spanLo) / bins;
  const out: Bin[] = Array.from({ length: bins }, (_, i) => ({
    from: fromSpace(spanLo + i * width),
    to: fromSpace(spanLo + (i + 1) * width),
    count: 0,
  }));
  for (const v of usable) {
    // The maximum would land one past the last bin; clamping keeps it counted
    // rather than dropping exactly the most extreme company on the chart.
    const idx = Math.min(bins - 1, Math.max(0, Math.floor((toSpace(v) - spanLo) / width)));
    (out[idx] as Bin).count += 1;
  }
  return {
    bins: out,
    median: percentile(ranked, 0.5),
    p10: percentile(ranked, 0.1),
    p90: percentile(ranked, 0.9),
    excluded,
    clipped,
  };
}

/** One stage of the conviction funnel. */
export interface Stage {
  label: string;
  count: number;
  /** Share of the stage above, which is what makes the narrowing legible. */
  ofPrevious: number;
  hint: string;
}

/**
 * How 2,700 companies become a shortlist.
 *
 * The tier counts are already on the page, but as five independent numbers:
 * nothing shows that each is a subset of the one before, which is the only
 * thing that makes "161 high conviction" mean anything. Stated as a funnel,
 * the selectivity is the message.
 *
 * Each stage is a strict superset of the next, so a reader can compare any two
 * directly. That is why "below an entry price" sums the two entry tiers rather
 * than showing them side by side -- they are the same test at two thresholds.
 */
export function funnel(tiers: Record<string, number>, universe: number): Stage[] {
  const at = (name: string) => tiers[name] ?? 0;
  const shortlisted = universe - at('None');
  const belowEntry = at('High conviction') + at('Below entry price');
  const raw: [string, number, string][] = [
    ['Screened', universe, 'Every company with enough financials to value.'],
    ['Reached a tier', shortlisted, 'Cheap enough, on any measure, to be worth a look.'],
    ['Below an entry price', belowEntry,
      'Trading under the margin of safety, not merely under the target.'],
    ['High conviction', at('High conviction'),
      'Below the tighter entry price, six or more BUY signals, all three models agreeing.'],
  ];
  return raw.map(([label, count, hint], i) => ({
    label,
    count,
    ofPrevious: i === 0 || raw[i - 1]![1] === 0 ? 100 : (count / raw[i - 1]![1]) * 100,
    hint,
  }));
}

/** One promoter-holding band and what the companies in it look like. */
export interface Band {
  label: string;
  from: number;
  to: number;
  count: number;
  /** Median upside of the band. Null when nothing in it has a usable target. */
  medianUpside: number | null;
}

const BAND_EDGES: [number, number, string][] = [
  [0, 25, 'Under 25%'],
  [25, 50, '25–50%'],
  [50, 75, '50–75%'],
  [75, 101, 'Over 75%'],
];

/**
 * Median upside by how much of the company the promoters still own.
 *
 * The median, not the mean. Upside here reaches 1,929,444% on a handful of
 * companies whose modelled EBITDA is near zero, and a single one of those
 * drags a band's mean past every real company in it -- the bar would then be
 * describing one data error rather than four hundred businesses.
 *
 * Bands rather than a scatter because the relationship is weak and a cloud of
 * 2,700 points invites a reader to see a trend in it. Four bars either differ
 * or they do not.
 */
export function holdingBands(
  rows: { promoter_holding: number | null; upside_pct: number | null }[],
): Band[] {
  return BAND_EDGES.map(([from, to, label]) => {
    const inBand = rows.filter(
      (r) => typeof r.promoter_holding === 'number'
        && r.promoter_holding >= from && r.promoter_holding < to,
    );
    const upsides = inBand
      .map((r) => r.upside_pct)
      .filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
      .sort((a, b) => a - b);
    return {
      label, from, to,
      count: inBand.length,
      medianUpside: upsides.length
        ? (upsides[Math.floor(upsides.length / 2)] as number)
        : null,
    };
  });
}
