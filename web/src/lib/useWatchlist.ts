/**
 * The watchlist, in the browser and in Supabase when there is an account.
 *
 * Local first, always. A reader who never signs in still gets a watchlist, and
 * one who does gets the same list on their other device -- so the local copy is
 * the source of truth for rendering and the remote one is a sync target, not a
 * dependency. A page that waited on the network to know whether a star was
 * filled would flicker on every load.
 *
 * Signing in merges rather than replaces. Somebody who watched six companies and
 * then signed in has not asked to lose them.
 */

import { useCallback, useEffect, useState } from 'react';

import { supabase } from './supabase';

const KEY = 'mcfinex:v1:watchlist';

function readLocal(): string[] {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    // A corrupt or unavailable store costs the watchlist, not the page.
    return [];
  }
}

function writeLocal(ids: string[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify([...new Set(ids)].sort()));
  } catch {
    /* private browsing, quota, or storage disabled */
  }
}

export interface Watchlist {
  ids: Set<string>;
  isWatched: (id: string) => boolean;
  toggle: (id: string) => void;
  /** True once a signed-in reader's remote list has been merged in. */
  synced: boolean;
}

export function useWatchlist(userId: string | null): Watchlist {
  const [ids, setIds] = useState<Set<string>>(() => new Set(readLocal()));
  const [synced, setSynced] = useState(false);

  // Pull the remote list and merge, once per sign-in.
  useEffect(() => {
    if (!supabase || !userId) {
      setSynced(false);
      return;
    }
    let alive = true;
    void (async () => {
      const { data, error } = await supabase
        .from('watchlist')
        .select('company_id')
        .eq('user_id', userId);
      if (!alive) return;
      if (error) {
        // Reported but not surfaced: the list still works locally, and a banner
        // about a sync failure is noise to someone who just wanted the screen.
        console.warn('watchlist sync failed', error.message);
        return;
      }
      const remote = (data ?? []).map((r) => r.company_id as string);
      const merged = new Set([...readLocal(), ...remote]);
      writeLocal([...merged]);
      setIds(merged);
      setSynced(true);

      // Push anything the remote did not have. Upsert so a row that arrived
      // from another tab in the meantime is not a conflict.
      const missing = [...merged].filter((id) => !remote.includes(id));
      if (missing.length) {
        await supabase
          .from('watchlist')
          .upsert(missing.map((company_id) => ({ user_id: userId, company_id })));
      }
    })();
    return () => {
      alive = false;
    };
  }, [userId]);

  const toggle = useCallback(
    (id: string) => {
      setIds((current) => {
        const next = new Set(current);
        const removing = next.has(id);
        if (removing) next.delete(id);
        else next.add(id);
        writeLocal([...next]);

        if (supabase && userId) {
          // Fire and forget. The local write already happened, so a failed
          // round trip costs the sync and not the interaction.
          const query = removing
            ? supabase.from('watchlist').delete()
                .eq('user_id', userId).eq('company_id', id)
            : supabase.from('watchlist')
                .upsert({ user_id: userId, company_id: id });
          void Promise.resolve(query).then(({ error }) => {
            if (error) console.warn('watchlist write failed', error.message);
          });
        }
        return next;
      });
    },
    [userId],
  );

  const isWatched = useCallback((id: string) => ids.has(id), [ids]);
  return { ids, isWatched, toggle, synced };
}
