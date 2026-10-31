/**
 * Axis bounds that survive the outliers in this data.
 *
 * The measures here are ratios of published figures, and a few of them are
 * absurd: P/E runs to 31,244 against a median of 29.8, and modelled upside to
 * 1,929,444% against a median of 35. Scaled to the extremes, a linear axis puts
 * the entire market inside the first pixel column -- which is exactly what the
 * first version of the scatter did, and it read as a broken chart rather than
 * as a chart of broken numbers.
 *
 * So the axis is drawn over the middle of the distribution and the rest is
 * reported rather than silently cropped. A reader who is not told how many
 * points are off-screen is being shown a subset presented as the whole.
 */

export interface Bounds {
  min: number;
  max: number;
  /** Points lying outside, which the chart will clip. */
  outside: number;
}

/**
 * The central span of `values`, by percentile.
 *
 * Percentiles rather than standard deviations: these distributions are nothing
 * like normal, and a mean two orders of magnitude above the median takes any
 * sigma-based window with it.
 */
export function robustBounds(
  values: number[],
  lower = 0.02,
  upper = 0.98,
  log = false,
): Bounds | null {
  // On a log axis the percentiles have to be taken in log space, or the padding
  // below is computed in the wrong units and a "5%" margin at the bottom of the
  // range is a different width from the one at the top.
  const usable = log
    ? values.filter((v) => Number.isFinite(v) && v > 0)
    : values.filter((v) => Number.isFinite(v));
  const sorted = usable.sort((a, b) => a - b);
  if (sorted.length === 0) return null;

  const at = (p: number) =>
    sorted[Math.min(sorted.length - 1, Math.max(0, Math.floor(sorted.length * p)))] as number;

  let min = at(lower);
  let max = at(upper);

  if (log) {
    // Pad multiplicatively: a decade is a decade wherever it sits.
    const span = Math.log10(max) - Math.log10(min);
    const pad = span > 0 ? span * 0.05 : 0.1;
    const lo = 10 ** (Math.log10(min) - pad);
    const hi = 10 ** (Math.log10(max) + pad);
    return {
      min: round(lo),
      max: round(hi),
      outside: sorted.filter((v) => v < lo || v > hi).length,
    };
  }

  if (min === max) {
    // Every value in the window is identical, so a span has to be invented or
    // the axis collapses and nothing is drawn at all.
    const pad = Math.abs(min) * 0.1 || 1;
    min -= pad;
    max += pad;
  } else {
    const pad = (max - min) * 0.05;
    min -= pad;
    max += pad;
  }

  // A measure that cannot be negative should not be given a negative axis just
  // because the padding reached below zero; it wastes half the plot.
  if (sorted[0]! >= 0 && min < 0) min = 0;

  return {
    min: round(min),
    max: round(max),
    outside: sorted.filter((v) => v < min || v > max).length,
  };
}

/** Enough precision to be honest, few enough digits to read on an axis. */
function round(value: number): number {
  const magnitude = Math.abs(value);
  if (magnitude >= 100) return Math.round(value);
  if (magnitude >= 1) return Math.round(value * 10) / 10;
  return Math.round(value * 1000) / 1000;
}
