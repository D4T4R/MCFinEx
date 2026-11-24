import { describe, expect, it } from 'vitest';

import { distribution, funnel, holdingBands } from './shapes';

describe('distribution', () => {
  it('places every usable value in exactly one bin', () => {
    const values = Array.from({ length: 500 }, (_, i) => i + 1);
    const d = distribution(values, { bins: 10 });
    expect(d.bins.reduce((n, b) => n + b.count, 0)).toBe(500);
  });

  it('counts the maximum rather than dropping it off the end', () => {
    // The largest value lands one past the last edge; unclamped it would be
    // silently discarded -- and it is exactly the most extreme company, the
    // one a reader is most likely to be looking for.
    const d = distribution([1, 2, 3, 100], { bins: 4 });
    expect(d.bins.reduce((n, b) => n + b.count, 0)).toBe(4);
    expect(d.bins[d.bins.length - 1]!.count).toBe(1);
  });

  it('bins in log space when the axis is logarithmic', () => {
    // The reason this exists. P/E runs from under 1 to 27,294 against a median
    // of 23.4: in linear bins the first bar holds nearly everything and the
    // chart reads as broken rather than as a skewed market.
    const values = [1, 10, 100, 1000, 10000];
    const linear = distribution(values, { bins: 5 });
    const log = distribution(values, { log: true, bins: 5 });
    expect(linear.bins[0]!.count).toBe(4);          // all but the largest
    expect(log.bins.map((b) => b.count)).toEqual([1, 1, 1, 1, 1]);
  });

  it('excludes non-positive values on a log axis and says how many', () => {
    // A reader shown a subset must be told it is a subset.
    const d = distribution([-5, 0, 1, 10, 100], { log: true, bins: 4 });
    expect(d.excluded).toBe(2);
    expect(d.bins.reduce((n, b) => n + b.count, 0)).toBe(3);
  });

  it('excludes nulls and non-finite values', () => {
    const d = distribution([1, null, undefined, NaN, Infinity, 2], { bins: 4 });
    expect(d.excluded).toBe(4);
  });

  it('takes percentiles over what was plotted, not the input', () => {
    // Otherwise the median line can sit outside the bars it describes.
    const d = distribution([-100, -50, 10, 20, 30], { log: true, bins: 4 });
    expect(d.median).toBe(20);
    expect(d.median!).toBeGreaterThanOrEqual(d.bins[0]!.from);
  });

  it('survives every value being identical', () => {
    // Zero span would otherwise divide by zero and produce NaN edges, which
    // renders as an empty chart with no error.
    const d = distribution([7, 7, 7], { bins: 10 });
    expect(d.bins).toHaveLength(1);
    expect(d.bins[0]!.count).toBe(3);
    expect(d.median).toBe(7);
  });

  it('returns an empty shape rather than throwing on no data', () => {
    const d = distribution([null, null]);
    expect(d.bins).toEqual([]);
    expect(d.median).toBeNull();
    expect(d.excluded).toBe(2);
    expect(d.clipped).toBe(0);
  });

  it('bins over the clamp window instead of the full range', () => {
    // The reason the clamp exists. Unclamped, upside % runs from -280,358 to
    // 1,929,444 and twenty-eight linear bins put every real company in one bar.
    const values = [-280_358, ...Array.from({ length: 50 }, (_, i) => i), 1_929_444];
    const loose = distribution(values, { bins: 10 });
    const tight = distribution(values, { bins: 10, min: 0, max: 49 });
    // Unclamped, all fifty real companies share a single bar and the other two
    // bars are the outliers themselves.
    expect(Math.max(...loose.bins.map((b) => b.count))).toBe(50);
    // Clamped, they spread across every bin.
    expect(tight.bins.every((b) => b.count > 0)).toBe(true);
  });

  it('counts values beyond the window as clipped, not as excluded', () => {
    // Two different facts about a company: one has nothing to plot, the other
    // is real and deliberately left off the end of the axis.
    const d = distribution([null, -500, 1, 2, 3, 900], { bins: 4, min: 0, max: 10 });
    expect(d.excluded).toBe(1);
    expect(d.clipped).toBe(2);
    expect(d.bins.reduce((n, b) => n + b.count, 0)).toBe(3);
  });

  it('reports the real median, not the median of the clamped window', () => {
    // The window is a percentile band, so the median still falls inside the
    // bars -- but it has to be the market's median, not the window's.
    const values = Array.from({ length: 101 }, (_, i) => i);
    const d = distribution(values, { bins: 10, min: 40, max: 60 });
    expect(d.median).toBe(50);
    expect(d.p90).toBe(90);
  });

  it('clamps in log space without dropping the window edges', () => {
    const d = distribution([1, 10, 100, 1000, 10000], { log: true, bins: 4, min: 10, max: 1000 });
    expect(d.clipped).toBe(2);
    expect(d.bins.reduce((n, b) => n + b.count, 0)).toBe(3);
  });
});

describe('funnel', () => {
  const tiers = {
    'High conviction': 161, 'Below entry price': 1319,
    'Re-rating': 141, Watch: 247, None: 854,
  };

  it('each stage is a subset of the one above it', () => {
    // The only thing that makes "161 high conviction" mean anything.
    const stages = funnel(tiers, 2722);
    for (let i = 1; i < stages.length; i++) {
      expect(stages[i]!.count).toBeLessThanOrEqual(stages[i - 1]!.count);
    }
  });

  it('counts the shortlist as everything that reached a tier', () => {
    const stages = funnel(tiers, 2722);
    expect(stages[0]!.count).toBe(2722);
    expect(stages[1]!.count).toBe(2722 - 854);
  });

  it('sums the two entry tiers, because they are one test at two thresholds', () => {
    const stages = funnel(tiers, 2722);
    expect(stages[2]!.count).toBe(161 + 1319);
  });

  it('reports each stage as a share of the one above', () => {
    const stages = funnel(tiers, 2722);
    expect(stages[0]!.ofPrevious).toBe(100);
    expect(stages[3]!.ofPrevious).toBeCloseTo((161 / 1480) * 100, 5);
  });

  it('does not divide by zero on an empty screen', () => {
    const stages = funnel({}, 0);
    expect(stages.every((s) => Number.isFinite(s.ofPrevious))).toBe(true);
  });

  it('treats a missing tier as none of it, not as a crash', () => {
    expect(() => funnel({ None: 5 }, 10)).not.toThrow();
  });
});

describe('holdingBands', () => {
  const row = (promoter_holding: number | null, upside_pct: number | null) =>
    ({ promoter_holding, upside_pct });

  it('uses the median, so one absurd upside cannot define a band', () => {
    // Upside reaches 1,929,444% on companies whose modelled EBITDA is near
    // zero. A mean would let a single data error describe the whole band.
    const bands = holdingBands([
      row(80, 10), row(81, 20), row(82, 30), row(83, 1_929_444),
    ]);
    const top = bands[3]!;
    expect(top.count).toBe(4);
    expect(top.medianUpside).toBe(30);
  });

  it('puts each company in exactly one band', () => {
    const rows = Array.from({ length: 100 }, (_, i) => row(i, 5));
    const bands = holdingBands(rows);
    expect(bands.reduce((n, b) => n + b.count, 0)).toBe(100);
  });

  it('includes 100% holding rather than dropping it off the top edge', () => {
    // A wholly promoter-owned company is real, and an upper edge of exactly
    // 100 would silently exclude it.
    expect(holdingBands([row(100, 5)])[3]!.count).toBe(1);
  });

  it('ignores companies with no holding figure', () => {
    const bands = holdingBands([row(null, 10), row(60, 10)]);
    expect(bands.reduce((n, b) => n + b.count, 0)).toBe(1);
  });

  it('reports no median rather than zero for an empty band', () => {
    // Zero would plot as a real bar at the origin and read as "no upside".
    const bands = holdingBands([row(90, 10)]);
    expect(bands[0]!.count).toBe(0);
    expect(bands[0]!.medianUpside).toBeNull();
  });

  it('ignores a company whose upside is missing but still counts it', () => {
    const bands = holdingBands([row(90, null), row(91, 40)]);
    expect(bands[3]!.count).toBe(2);
    expect(bands[3]!.medianUpside).toBe(40);
  });
});
