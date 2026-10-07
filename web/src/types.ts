/**
 * The payload contract, mirroring `screen_payload` in src/mcfinex/publish.py.
 *
 * Field names and nullability were read off the real file over all 2,544
 * companies rather than written from the Python by eye. Anything the publisher
 * passes through `money()` can be null, so it is typed that way even where the
 * current data happens never to be -- a company seeded last night and scraped
 * this morning has holes a fully scraped one does not.
 *
 * Every payload carries `schema`. Unlike the phone app this page cannot be left
 * on an old build, so a mismatch here is a bug rather than a stale install --
 * but it is still worth refusing to render numbers under a contract we no longer
 * understand.
 */

export const SUPPORTED_SCHEMA = 1;

/** The shortlists, worst to best, as `publish.py` spells them. */
export const TIERS = [
  'High conviction',
  'Below entry price',
  'Re-rating',
  'Watch',
  'None',
] as const;

export type Tier = (typeof TIERS)[number];

/** What each tier means, for a reader who has not read the methodology. */
export const TIER_HELP: Record<Tier, string> = {
  'High conviction':
    'Below the 2/3 entry price and corroborated by all three valuation models.',
  'Below entry price': 'Below an entry price, but less corroborated.',
  'Re-rating':
    'Already past its entry price, target not yet reached, business still screens well.',
  Watch: 'Cheap on the headline model only.',
  None: 'Did not qualify for a shortlist.',
};

export interface Company {
  /** Filename stem; differs from `ticker` where a ticker contains `&`. */
  id: string;
  ticker: string;
  name: string | null;
  sector: string | null;
  tier: Tier;

  price: number | null;
  target: number | null;
  entry_3by4: number | null;
  entry_2by3: number | null;
  upside_pct: number | null;
  discount_to_entry_pct: number | null;
  actionable: boolean;

  buy_signals: number;
  sell_signals: number;
  scored: number;
  quality_buys: number;
  quality_sells: number;
  models_agreeing: number;
  flags: string[];

  /** Rupees crore. No signal reads it; it is the scale a reader judges by. */
  market_cap: number | null;
  stock_pe: number | null;
  sector_pe: number | null;
  roce: number | null;
  dividend_yield: number | null;
  book_value: number | null;
  price_to_book: number | null;
  promoter_holding: number | null;
  free_cash_flow: number | null;

  quarters_reported: number;
  is_financial: boolean;
}

export interface SectorHeat {
  sector: string;
  picks: number;
  total: number;
  share_pct: number;
  median_upside_pct: number;
}

export interface ScreenPayload {
  schema: number;
  generated: string;
  price_date: string | null;
  last_scraped: string | null;
  /** Everything screened, including the names that reached no tier. */
  universe: number;
  tiers: Partial<Record<Tier, number>>;
  sectors: SectorHeat[];
  companies: Company[];
  disclaimer: string;
  disclaimer_full: string;
}

/** Upper case, as `screening.Verdict` serialises. */
export type Verdict = 'BUY' | 'HOLD' | 'SELL' | 'UNKNOWN';
export type Confidence = 'HIGH' | 'MEDIUM' | 'LOW' | 'NONE';

export interface Signal {
  key: string;
  label: string;
  verdict: Verdict;
  value: number | null;
  /** The threshold in words. Not a remedy -- it describes, it does not instruct. */
  rule: string;
  available: boolean;
}

export interface Trend {
  label: string;
  periods: string[];
  values: (number | null)[];
  /** A series aligned to periods[4:], not a scalar. */
  yoy_growth_pct: (number | null)[];
  ttm: number | null;
  ttm_prior: number | null;
  ttm_growth_pct: number | null;
  forecast: number | null;
  forecast_period: string | null;
  confidence: Confidence;
  note: string;
}

/**
 * `company/{id}.json`, fetched on demand.
 *
 * Deliberately not `Company & {...}`: the detail file carries the ranked-pick
 * fields and the signals, but none of the fundamentals that only screen.json
 * publishes -- no market cap, no ROCE, no P/E. Typing it as a superset of
 * Company would let a page read `detail.market_cap` and get undefined at
 * runtime with no complaint from the compiler.
 */
export interface CompanyDetail {
  schema: number;
  ticker: string;
  name: string | null;
  sector: string | null;
  tier: Tier;
  price: number | null;
  target: number | null;
  entry_3by4: number | null;
  entry_2by3: number | null;
  upside_pct: number | null;
  discount_to_entry_pct: number | null;
  actionable: boolean;
  buy_signals: number;
  sell_signals: number;
  scored: number;
  quality_buys: number;
  quality_sells: number;
  models_agreeing: number;
  flags: string[];
  targets: {
    ev_ebitda: number | null;
    pe_yearly: number | null;
    pe_quarterly: number | null;
  };
  signals: Signal[];
  trends: Trend[];
  disclaimer: string;
}
