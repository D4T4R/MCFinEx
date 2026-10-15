/**
 * Small labelled markers: tiers, verdicts, flags.
 *
 * Each carries a word as well as a colour. A tier shown only as a green dot is
 * unreadable to a reader who cannot separate it from the amber one, and this
 * screen leans on three of them at once.
 */

import type { Board, Tier, Verdict } from '../types';

const TIER_STYLE: Record<Tier, string> = {
  'High conviction': 'bg-buy/15 text-buy ring-1 ring-buy/30',
  'Below entry price': 'bg-accent/15 text-accent ring-1 ring-accent/30',
  'Re-rating': 'bg-hold/15 text-hold ring-1 ring-hold/30',
  Watch: 'bg-ink-800 text-ink-200 ring-1 ring-ink-600',
  None: 'bg-transparent text-ink-600 ring-1 ring-ink-800',
};

export function TierChip({ tier, title }: { tier: Tier; title?: string }) {
  return (
    <span className={`chip ${TIER_STYLE[tier]}`} title={title}>
      {tier}
    </span>
  );
}

const VERDICT_STYLE: Record<Verdict, string> = {
  BUY: 'bg-buy/15 text-buy ring-1 ring-buy/30',
  SELL: 'bg-sell/15 text-sell ring-1 ring-sell/30',
  HOLD: 'bg-hold/15 text-hold ring-1 ring-hold/30',
  UNKNOWN: 'bg-ink-850 text-ink-600 ring-1 ring-ink-800',
};

export function VerdictChip({ verdict }: { verdict: Verdict }) {
  return <span className={`chip ${VERDICT_STYLE[verdict]}`}>{verdict}</span>;
}

/**
 * A caveat on a company, e.g. "not enriched" or "upside implausibly large".
 *
 * Styled as a warning rather than an error: a flag qualifies a reading, it does
 * not invalidate the company, and the screen is advisory throughout.
 */
export function FlagChip({ flag }: { flag: string }) {
  return (
    <span className="chip bg-hold/10 text-hold/90 ring-1 ring-hold/25" title={flag}>
      {flag}
    </span>
  );
}

/** A count out of a total, as in "7/10 BUY". */
export function SignalCount({ buys, scored }: { buys: number; scored: number }) {
  const share = scored > 0 ? buys / scored : 0;
  const tone =
    share >= 0.6 ? 'text-buy' : share >= 0.4 ? 'text-hold' : 'text-ink-400';
  return (
    <span className="tnum whitespace-nowrap">
      <span className={tone}>{buys}</span>
      <span className="text-ink-600">/{scored}</span>
    </span>
  );
}

const BOARD_STYLE: Record<Board, string> = {
  // Muted on purpose. The board is context, not a verdict -- it should not
  // compete with the tier chip beside it, which is the actual judgement.
  Main: 'bg-ink-850 text-ink-300 ring-1 ring-ink-600',
  SME: 'bg-accent/10 text-accent ring-1 ring-accent/30',
};

const BOARD_HELP: Record<Board, string> = {
  Main: 'Main board of the exchange.',
  SME: 'SME platform. Smaller companies with lighter disclosure requirements, '
     + 'so every figure here rests on less.',
};

export function BoardChip({ board }: { board: Board | null }) {
  // Nothing at all rather than "unknown": an absent board is a gap in our data,
  // not a fact about the company, and a chip saying so would read as one.
  if (!board) return null;
  return (
    <span className={`chip ${BOARD_STYLE[board]}`} title={BOARD_HELP[board]}>
      {board}
    </span>
  );
}
