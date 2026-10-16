/**
 * Asking for a company the screen does not cover.
 *
 * The quota is two a week and it is enforced in the database policy, not here.
 * What this does is tell a reader where they stand before they press anything --
 * a button that fails on submit with a policy violation is worse than one that
 * says "none left until Tuesday".
 *
 * So the count shown is advisory and the count that matters is server-side. If
 * the two ever disagree the server wins, and the reader sees the real answer.
 */

import { useCallback, useEffect, useState } from 'react';

import { supabase } from './supabase';

export const WEEKLY_QUOTA = 2;

export interface RequestState {
  /** Tickers this reader has asked for and not yet had answered. */
  pending: Set<string>;
  /** Requests made in the last seven days, quota included. */
  used: number;
  remaining: number;
  loading: boolean;
  error: string | null;
  request: (ticker: string) => Promise<void>;
}

const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

export interface RequestRow {
  ticker: string;
  requested_at: string;
  promoted_at: string | null;
}

/**
 * What a reader has spent and what is still outstanding.
 *
 * Pure, and tested, because one line of it decides what the limit means.
 * Counting rows *made* in the window gives two a week; counting rows still
 * pending would give two at a time, and withdrawing a request would refund it
 * -- which is not a weekly quota, it is a queue depth. The database policy
 * counts the same way, so the two agree.
 */
export function quotaFrom(rows: RequestRow[]): {
  used: number;
  remaining: number;
  pending: Set<string>;
} {
  return {
    used: rows.length,
    remaining: Math.max(0, WEEKLY_QUOTA - rows.length),
    pending: new Set(rows.filter((r) => !r.promoted_at).map((r) => r.ticker)),
  };
}

export function useRequests(userId: string | null): RequestState {
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [used, setUsed] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!supabase || !userId) {
      setPending(new Set());
      setUsed(0);
      return;
    }
    const since = new Date(Date.now() - WEEK_MS).toISOString();
    const { data, error: failed } = await supabase
      .from('scrape_request')
      .select('ticker, requested_at, promoted_at')
      .gte('requested_at', since);
    if (failed) {
      setError(failed.message);
      return;
    }
    const quota = quotaFrom((data ?? []) as RequestRow[]);
    setUsed(quota.used);
    setPending(quota.pending);
  }, [userId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const request = useCallback(
    async (ticker: string) => {
      if (!supabase || !userId) return;
      setLoading(true);
      setError(null);
      const { error: failed } = await supabase
        .from('scrape_request')
        .insert({ user_id: userId, ticker });
      if (failed) {
        // The policy rejects an over-quota insert, and the message it returns
        // is Postgres's rather than anything a reader should read.
        setError(
          failed.code === '42501' || /row-level security/i.test(failed.message)
            ? `That is more than ${WEEKLY_QUOTA} requests in seven days.`
            : failed.message,
        );
      }
      await refresh();
      setLoading(false);
    },
    [userId, refresh],
  );

  return {
    pending,
    used,
    remaining: Math.max(0, WEEKLY_QUOTA - used),
    loading,
    error,
    request,
  };
}
