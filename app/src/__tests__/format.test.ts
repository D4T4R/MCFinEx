import { compact, freshness, humanDate, money, percent } from '../format';

describe('money', () => {
  it('groups digits the Indian way', () => {
    // 1,00,000 rather than 100,000: the app is read in India.
    expect(money(100000)).toBe('1,00,000.00');
  });

  it('shows a dash rather than zero when there is no figure', () => {
    // A missing price and a price of zero mean very different things.
    expect(money(null)).toBe('—');
    expect(money(0)).toBe('0.00');
  });
});

describe('percent', () => {
  it('signs the number, because the sign is the meaning', () => {
    expect(percent(18.4)).toBe('+18%');
    expect(percent(-3.2)).toBe('-3%');
  });

  it('has a dash for no value', () => {
    expect(percent(null)).toBe('—');
  });
});

describe('compact', () => {
  it('abbreviates large figures in lakhs', () => {
    expect(compact(155582)).toBe('1.56L');
    expect(compact(2400)).toBe('2.4k');
    expect(compact(88)).toBe('88');
  });
});

describe('humanDate', () => {
  it('reads a stamp as a date', () => {
    expect(humanDate('2026-09-04')).toBe('4 Sep 2026');
  });

  it('returns null for anything that is not one', () => {
    expect(humanDate('not a date')).toBeNull();
    expect(humanDate(null)).toBeNull();
  });
});

describe('freshness', () => {
  it('reports prices and fundamentals separately', () => {
    // They refresh on different cadences; one date would be wrong for one of
    // them, and misleading in the direction that matters.
    expect(freshness('2026-09-04', '2026-08-21')).toBe(
      'Prices as of 4 Sep 2026 · fundamentals to 21 Aug 2026 · prices refresh nightly',
    );
  });

  it('never tells a reader to run a command they cannot run', () => {
    expect(freshness('2026-09-04', '2026-08-21')).not.toContain('mcfinex');
  });

  it('says something sensible when it knows neither date', () => {
    expect(freshness(null, null)).toBe('Screened from stored data.');
  });
});
