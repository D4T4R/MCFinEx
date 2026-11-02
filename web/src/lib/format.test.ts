import { describe, expect, it } from 'vitest';

import { DASH, plain, unratedNote } from './format';

const RATED = { available: true, rule: '>10% below sector median BUY' };
const UNRATED = { available: false, rule: 'no sector median available' };

describe('unratedNote', () => {
  it('says why when the screen declines to rate the figure', () => {
    // Jio Platforms: a P/E of 1,019 with no sector assigned, so there is
    // nothing to judge it against. The number is still shown -- withholding a
    // figure the screen computed is its own kind of lie -- but it must not
    // read as a verdict.
    expect(unratedNote(1019, UNRATED)).toBe('not rated · no sector median available');
  });

  it('stays silent when the screen stands behind the figure', () => {
    expect(unratedNote(15.3, RATED)).toBeUndefined();
  });

  it('adds nothing to a figure that has no value', () => {
    // A dash already says there is nothing here; "not rated" on top of it
    // would imply a number exists that we are declining to judge.
    expect(unratedNote(null, UNRATED)).toBeUndefined();
    expect(plain(null)).toBe(DASH);
  });

  it('stays silent when the signal is missing entirely', () => {
    // The detail file failed to load. That is a fetch problem, reported
    // elsewhere, and guessing at a caveat here would be inventing one.
    expect(unratedNote(1019, undefined)).toBeUndefined();
  });

  it('does not suppress a figure the screen rates badly', () => {
    // A loss-making company gets a real verdict (SELL) on a negative P/E.
    // "Unrated" and "rated, and the answer is bad" are different claims.
    expect(unratedNote(-8, { available: true, rule: 'loss-making' })).toBeUndefined();
  });
});
