/**
 * The screener's predicate.
 *
 * Worth testing on its own because the table and the scatter plot both read it,
 * and because the interesting cases are all about *missing* data. Roughly a
 * thousand of the 2,544 published companies have no P/E, no ROCE or no book
 * value, so how a threshold treats a gap decides what a value screen shows --
 * and the wrong answer looks like a working screen full of the wrong companies.
 */

import { describe, expect, it } from 'vitest';

import { apply, EMPTY_FILTERS, isDefault, matches, sectorsOf } from './filters';
import { capBand } from './format';
import type { Company } from '../types';

function company(overrides: Partial<Company> = {}): Company {
  return {
    id: 'ACME', ticker: 'ACME', name: 'Acme Ltd', sector: 'Widgets',
    tier: 'High conviction',
    price: 100, target: 200, entry_3by4: 150, entry_2by3: 133,
    upside_pct: 60, discount_to_entry_pct: 25, actionable: true,
    buy_signals: 7, sell_signals: 1, scored: 10,
    quality_buys: 3, quality_sells: 0, models_agreeing: 3, flags: [],
    market_cap: 25000, stock_pe: 12, sector_pe: 20, roce: 22,
    dividend_yield: 2, book_value: 50, price_to_book: 2,
    promoter_holding: 60, free_cash_flow: 10,
    quarters_reported: 12, is_financial: false,
    ...overrides,
  };
}

const NONE = new Set<string>();

describe('missing measures never satisfy a threshold', () => {
  it('a company with no P/E fails "P/E at most 20"', () => {
    // The case that matters. Treating null as 0 puts every loss-making and
    // unscraped company at the top of a "cheapest first" screen.
    const filters = { ...EMPTY_FILTERS, peMax: 20 };
    expect(matches(company({ stock_pe: null }), filters, NONE)).toBe(false);
  });

  it('a negative P/E is a loss, not a bargain', () => {
    const filters = { ...EMPTY_FILTERS, peMax: 20 };
    expect(matches(company({ stock_pe: -8 }), filters, NONE)).toBe(false);
  });

  it('a company with no ROCE fails a minimum ROCE', () => {
    const filters = { ...EMPTY_FILTERS, roceMin: 15 };
    expect(matches(company({ roce: null }), filters, NONE)).toBe(false);
  });

  it('a company with no upside fails a minimum upside', () => {
    const filters = { ...EMPTY_FILTERS, upsideMin: 20 };
    expect(matches(company({ upside_pct: null }), filters, NONE)).toBe(false);
  });

  it('a company with no sector fails a sector filter', () => {
    const filters = { ...EMPTY_FILTERS, sectors: ['Widgets'] };
    expect(matches(company({ sector: null }), filters, NONE)).toBe(false);
  });

  it('a company with no market cap fails a cap band', () => {
    const filters = { ...EMPTY_FILTERS, capBands: ['Large' as const] };
    expect(matches(company({ market_cap: null }), filters, NONE)).toBe(false);
  });

  it('but an unset threshold does not exclude a gap', () => {
    // The other half: no filter means no opinion, so a company with holes still
    // appears in an unfiltered screen.
    const bare = company({ stock_pe: null, roce: null, upside_pct: null,
                           market_cap: null, sector: null });
    expect(matches(bare, EMPTY_FILTERS, NONE)).toBe(true);
  });
});

describe('search', () => {
  it('matches a ticker case-insensitively', () => {
    expect(matches(company(), { ...EMPTY_FILTERS, search: 'acme' }, NONE)).toBe(true);
  });

  it('matches part of a company name', () => {
    expect(matches(company(), { ...EMPTY_FILTERS, search: 'cme lt' }, NONE)).toBe(true);
  });

  it('ignores surrounding whitespace', () => {
    expect(matches(company(), { ...EMPTY_FILTERS, search: '  ACME  ' }, NONE)).toBe(true);
  });

  it('excludes a company it does not match', () => {
    expect(matches(company(), { ...EMPTY_FILTERS, search: 'reliance' }, NONE)).toBe(false);
  });

  it('survives a company with no name', () => {
    expect(matches(company({ name: null }), { ...EMPTY_FILTERS, search: 'acme' }, NONE))
      .toBe(true);
  });
});

describe('signal counts', () => {
  it('a minimum BUY count excludes below it', () => {
    expect(matches(company({ buy_signals: 5 }), { ...EMPTY_FILTERS, minBuys: 7 }, NONE))
      .toBe(false);
  });

  it('a maximum SELL count excludes above it', () => {
    expect(matches(company({ sell_signals: 4 }), { ...EMPTY_FILTERS, maxSells: 2 }, NONE))
      .toBe(false);
  });

  it('the default SELL ceiling excludes nobody', () => {
    expect(matches(company({ sell_signals: 10 }), EMPTY_FILTERS, NONE)).toBe(true);
  });
});

describe('restrictions', () => {
  it('actionable only keeps companies at or below an entry price', () => {
    const filters = { ...EMPTY_FILTERS, actionableOnly: true };
    expect(matches(company({ actionable: false }), filters, NONE)).toBe(false);
    expect(matches(company({ actionable: true }), filters, NONE)).toBe(true);
  });

  it('excluding new listings drops companies with no reported quarter', () => {
    const filters = { ...EMPTY_FILTERS, excludeNewListings: true };
    expect(matches(company({ quarters_reported: 0 }), filters, NONE)).toBe(false);
    expect(matches(company({ quarters_reported: 1 }), filters, NONE)).toBe(true);
  });

  it('excluding financials drops banks and NBFCs', () => {
    const filters = { ...EMPTY_FILTERS, excludeFinancials: true };
    expect(matches(company({ is_financial: true }), filters, NONE)).toBe(false);
  });

  it('watchlist only reads the set it is given', () => {
    const filters = { ...EMPTY_FILTERS, watchedOnly: true };
    expect(matches(company(), filters, NONE)).toBe(false);
    expect(matches(company(), filters, new Set(['ACME']))).toBe(true);
  });
});

describe('composition', () => {
  it('filters narrow together rather than replacing each other', () => {
    const rows = [
      company({ id: 'A', ticker: 'A', stock_pe: 10, roce: 30 }),
      company({ id: 'B', ticker: 'B', stock_pe: 10, roce: 5 }),
      company({ id: 'C', ticker: 'C', stock_pe: 40, roce: 30 }),
    ];
    const got = apply(rows, { ...EMPTY_FILTERS, peMax: 20, roceMin: 15 }, NONE);
    expect(got.map((c) => c.ticker)).toEqual(['A']);
  });

  it('an empty filter set returns everything', () => {
    const rows = [company({ id: 'A' }), company({ id: 'B' })];
    expect(apply(rows, EMPTY_FILTERS, NONE)).toHaveLength(2);
  });
});

describe('isDefault', () => {
  it('is true for the empty set', () => {
    expect(isDefault(EMPTY_FILTERS)).toBe(true);
  });

  it('is true when the only input is whitespace', () => {
    // Otherwise a stray space leaves "Clear filters" showing with nothing to
    // clear, which reads as the screen being narrowed when it is not.
    expect(isDefault({ ...EMPTY_FILTERS, search: '   ' })).toBe(true);
  });

  it('is false once something is set', () => {
    expect(isDefault({ ...EMPTY_FILTERS, minBuys: 1 })).toBe(false);
    expect(isDefault({ ...EMPTY_FILTERS, tiers: ['Watch'] })).toBe(false);
  });
});

describe('cap bands', () => {
  it('sort a company into exactly one band', () => {
    expect(capBand(50000)).toBe('Large');
    expect(capBand(10000)).toBe('Mid');
    expect(capBand(1000)).toBe('Small');
    expect(capBand(100)).toBe('Micro');
  });

  it('put the boundary in the higher band, not both', () => {
    expect(capBand(20000)).toBe('Large');
    expect(capBand(5000)).toBe('Mid');
    expect(capBand(500)).toBe('Small');
  });

  it('return null for an unknown cap rather than guessing Micro', () => {
    expect(capBand(null)).toBeNull();
  });
});

describe('sectorsOf', () => {
  it('is distinct, sorted and free of nulls', () => {
    const rows = [
      company({ sector: 'Widgets' }), company({ sector: 'Banks' }),
      company({ sector: 'Widgets' }), company({ sector: null }),
    ];
    expect(sectorsOf(rows)).toEqual(['Banks', 'Widgets']);
  });
});
