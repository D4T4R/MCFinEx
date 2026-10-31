/**
 * Axis bounds.
 *
 * The measures this plots are mostly log-normal, and drawing them linearly put
 * 77% of 2,317 companies inside the leftmost tenth of the axis -- a smear
 * against nine-tenths of empty canvas. In log space the same points spread
 * across seven deciles. These tests pin the two things that makes possible:
 * percentiles taken in log space, and non-positive values excluded rather than
 * silently producing NaN bounds.
 */

import { describe, expect, it } from 'vitest';

import { robustBounds } from './scale';

const decades = [1, 3, 10, 30, 100, 300, 1000, 3000];

describe('log bounds', () => {
  it('pads multiplicatively, so a decade is a decade wherever it sits', () => {
    const b = robustBounds(decades, 0, 1, true)!;
    // Equal padding in log space means the ratio below the min matches the
    // ratio above the max; padding additively would make the low end negative.
    const below = decades[0]! / b.min;
    const above = b.max / decades[decades.length - 1]!;
    expect(Math.abs(below - above)).toBeLessThan(0.05);
  });

  it('never produces a non-positive lower bound', () => {
    // A log axis cannot draw zero, so padding must not reach it.
    const b = robustBounds([1, 2, 3], 0, 1, true)!;
    expect(b.min).toBeGreaterThan(0);
  });

  it('drops non-positive values rather than returning NaN', () => {
    // Loss-making companies have a negative P/E. log10 of those is NaN, and a
    // NaN bound collapses the whole axis and renders nothing.
    const b = robustBounds([-50, 0, 10, 20, 30], 0, 1, true)!;
    expect(Number.isFinite(b.min)).toBe(true);
    expect(Number.isFinite(b.max)).toBe(true);
    expect(b.min).toBeGreaterThan(0);
  });

  it('returns null when nothing is plottable on a log axis', () => {
    expect(robustBounds([-5, -1, 0], 0, 1, true)).toBeNull();
  });

  it('spreads a skewed distribution better than a linear window', () => {
    // The actual complaint: P/E runs 6 to 538 with a median of 30.
    const skewed = Array.from({ length: 1000 }, (_, i) => 6 * Math.pow(1.0045, i));
    const lin = robustBounds(skewed, 0.02, 0.98, false)!;
    const log = robustBounds(skewed, 0.02, 0.98, true)!;
    const share = (b: { min: number; max: number }, toLog: boolean) => {
      const f = toLog ? Math.log10 : (v: number) => v;
      const lo = f(b.min), hi = f(b.max);
      const inFirstTenth = skewed.filter((v) => f(v) <= lo + (hi - lo) / 10).length;
      return inFirstTenth / skewed.length;
    };
    expect(share(lin, false)).toBeGreaterThan(0.3);
    expect(share(log, true)).toBeLessThan(0.15);
  });
});

describe('linear bounds still behave', () => {
  it('keeps a measure that legitimately goes negative', () => {
    const b = robustBounds([-20, -5, 0, 5, 20], 0, 1, false)!;
    expect(b.min).toBeLessThan(0);
  });

  it('does not invent a negative floor for a non-negative measure', () => {
    // Half the plot wasted below zero for a quantity that cannot be negative.
    const b = robustBounds([0, 1, 2, 3], 0, 1, false)!;
    expect(b.min).toBe(0);
  });
});
