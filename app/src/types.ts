/**
 * The shape of what `mcfinex publish` writes.
 *
 * Mirrors src/mcfinex/publish.py. The two live in one repository so a payload
 * change and the code that reads it land in the same commit -- the app is
 * sideloaded, so there is no way to push an update to anyone who has it.
 */

/** The highest payload version this build understands. */
export const SUPPORTED_SCHEMA = 1;

export type Tier =
  | 'High conviction'
  | 'Below entry price'
  | 'Re-rating'
  | 'Watch'
  | 'None';

/** Browse order: strength of claim, strongest first. Matches picks._TIER_ORDER. */
export const TIERS: Tier[] = [
  'High conviction',
  'Below entry price',
  'Re-rating',
  'Watch',
];

export const TIER_HELP: Record<string, string> = {
  'High conviction':
    'Below the 2/3 entry price, six or more fundamental BUY signals, and all ' +
    'three valuation models agreeing. Excludes new listings and financials.',
  'Below entry price': 'Below the 3/4 entry price, but less corroborated.',
  'Re-rating':
    'Already run past the entry price, with the EV/EBITDA target still ahead. ' +
    'Four or more of the seven business-quality signals BUY and none of them ' +
    'SELL — P/E and price-to-book are ignored here, because a share that has ' +
    'risen fails those for having risen. Not a margin-of-safety buy: the ' +
    'discount is gone, only the headroom is left.',
  Watch: 'Cheap on the EV/EBITDA model alone. Least corroborated.',
};

/** UNKNOWN is not a neutral reading: the input was missing, so no view is offered. */
export type Verdict = 'BUY' | 'HOLD' | 'SELL' | 'UNKNOWN';

export type Confidence = 'HIGH' | 'MEDIUM' | 'LOW' | 'NONE';

export interface Pick {
  /** Filename stem for the detail file; the ticker with `&` mapped to `_`. */
  id: string;
  ticker: string;
  name: string | null;
  sector: string | null;
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
  models_agreeing: number;
  /** Score with the price-based signals removed, so a risen share is not marked down. */
  quality_buys: number;
  quality_sells: number;
  tier: Tier;
  flags: string[];
}

export interface SectorHeat {
  sector: string;
  picks: number;
  total: number;
  share_pct: number | null;
  median_upside_pct: number | null;
}

export interface IndexPayload {
  schema: number;
  generated: string;
  price_date: string | null;
  last_scraped: string | null;
  /** Everything screened, including names that reached no tier. */
  universe: number;
  tiers: Record<string, number>;
  sectors: SectorHeat[];
  picks: Pick[];
  disclaimer: string;
  disclaimer_full: string;
}

export interface Signal {
  key: string;
  label: string;
  verdict: Verdict;
  value: number | null;
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

/** The detail payload has no `id`: it was fetched by one. */
export interface Company extends Omit<Pick, 'id'> {
  schema: number;
  targets: {
    ev_ebitda: number | null;
    pe_yearly: number | null;
    pe_quarterly: number | null;
  };
  signals: Signal[];
  trends: Trend[];
  disclaimer: string;
}
