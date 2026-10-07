/**
 * What the screener is currently showing.
 *
 * Pure, and separate from every component that renders it. The table and the
 * scatter plot show the same selection from two directions -- brushing the chart
 * narrows the table -- so if the predicate lived in either one they would drift
 * and a reader would see two different answers to the same question.
 */

import { capBand, type CapBand } from './format';
import type { Company, Tier } from '../types';

export interface Filters {
  /** Matched against ticker and company name, case-insensitively. */
  search: string;
  tiers: Tier[];
  sectors: string[];
  capBands: CapBand[];
  minBuys: number;
  maxSells: number;
  /** Only companies at or below an entry price. */
  actionableOnly: boolean;
  /** Hide companies with no reported quarters as a listed entity. */
  excludeNewListings: boolean;
  /** Hide banks and NBFCs, whose EV/EBITDA is not meaningful. */
  excludeFinancials: boolean;
  /** Only the reader's watchlist. */
  watchedOnly: boolean;
  upsideMin: number | null;
  peMax: number | null;
  roceMin: number | null;
}

export const EMPTY_FILTERS: Filters = {
  search: '',
  tiers: [],
  sectors: [],
  capBands: [],
  minBuys: 0,
  maxSells: 10,
  actionableOnly: false,
  excludeNewListings: false,
  excludeFinancials: false,
  watchedOnly: false,
  upsideMin: null,
  peMax: null,
  roceMin: null,
};

export function isDefault(filters: Filters): boolean {
  return (
    JSON.stringify({ ...filters, search: filters.search.trim() }) ===
    JSON.stringify(EMPTY_FILTERS)
  );
}

/**
 * Whether one company survives the filters.
 *
 * A null measure never satisfies a threshold. "P/E at most 20" asked of a
 * company with no P/E has no true answer, and treating the gap as a pass would
 * quietly fill a value screen with companies that have no value to compare --
 * which reads as a bug in the screen rather than a hole in the data.
 */
export function matches(
  company: Company,
  filters: Filters,
  watched: Set<string>,
): boolean {
  const needle = filters.search.trim().toLowerCase();
  if (needle) {
    const haystack = `${company.ticker} ${company.name ?? ''}`.toLowerCase();
    if (!haystack.includes(needle)) return false;
  }
  if (filters.tiers.length && !filters.tiers.includes(company.tier)) return false;
  if (filters.sectors.length) {
    if (!company.sector || !filters.sectors.includes(company.sector)) return false;
  }
  if (filters.capBands.length) {
    const band = capBand(company.market_cap);
    if (!band || !filters.capBands.includes(band)) return false;
  }
  if (company.buy_signals < filters.minBuys) return false;
  if (company.sell_signals > filters.maxSells) return false;
  if (filters.actionableOnly && !company.actionable) return false;
  if (filters.excludeNewListings && company.quarters_reported === 0) return false;
  if (filters.excludeFinancials && company.is_financial) return false;
  if (filters.watchedOnly && !watched.has(company.id)) return false;

  if (filters.upsideMin != null) {
    if (company.upside_pct == null || company.upside_pct < filters.upsideMin) return false;
  }
  if (filters.peMax != null) {
    // A negative P/E is a loss-making company, not a cheap one, so it fails a
    // "P/E at most N" test rather than passing it by being the smallest number.
    if (company.stock_pe == null || company.stock_pe <= 0) return false;
    if (company.stock_pe > filters.peMax) return false;
  }
  if (filters.roceMin != null) {
    if (company.roce == null || company.roce < filters.roceMin) return false;
  }
  return true;
}

export function apply(
  companies: Company[],
  filters: Filters,
  watched: Set<string>,
): Company[] {
  return companies.filter((c) => matches(c, filters, watched));
}

/** Distinct sectors present, for the sector picker. */
export function sectorsOf(companies: Company[]): string[] {
  return [...new Set(companies.map((c) => c.sector).filter((s): s is string => !!s))]
    .sort((a, b) => a.localeCompare(b));
}
