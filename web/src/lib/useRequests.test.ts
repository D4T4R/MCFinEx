/**
 * The request quota.
 *
 * One line of `quotaFrom` decides whether the limit means "two a week" or "two
 * at a time", and the difference only shows up when somebody withdraws a
 * request -- which is exactly when a reader is trying to get a third.
 *
 * The real enforcement is the database policy; this is what the page shows
 * before anyone presses anything. They have to agree, so both count the same
 * thing: requests *made* in the window.
 */

import { describe, expect, it } from 'vitest';

import { quotaFrom, WEEKLY_QUOTA } from './useRequests';

const made = (ticker: string, promoted = false) => ({
  ticker,
  requested_at: '2026-10-15T10:00:00Z',
  promoted_at: promoted ? '2026-10-16T00:00:00Z' : null,
});

describe('quotaFrom', () => {
  it('spends the quota on requests made, not requests outstanding', () => {
    // Both answered already. The week is still spent -- otherwise the limit is
    // a queue depth and a reader gets two every night.
    const quota = quotaFrom([made('A', true), made('B', true)]);
    expect(quota.used).toBe(2);
    expect(quota.remaining).toBe(0);
  });

  it('reports what is still outstanding separately', () => {
    const quota = quotaFrom([made('A', true), made('B', false)]);
    expect([...quota.pending]).toEqual(['B']);
  });

  it('starts full', () => {
    expect(quotaFrom([]).remaining).toBe(WEEKLY_QUOTA);
  });

  it('never reports a negative remainder', () => {
    // The policy should prevent a third, but a stale read or a race must show
    // "none left" rather than "-1 left".
    expect(quotaFrom([made('A'), made('B'), made('C')]).remaining).toBe(0);
  });

  it('counts a company asked for once as one', () => {
    expect(quotaFrom([made('A')]).used).toBe(1);
  });
});
