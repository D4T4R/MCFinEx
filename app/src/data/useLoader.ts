/** Loading state for a cached payload, with pull-to-refresh. */

import { useCallback, useEffect, useRef, useState } from 'react';

import { REFRESH_AFTER_MS } from '../config';
import { Loaded } from './remote';

export interface LoaderState<T> {
  data?: T;
  error?: Error;
  loading: boolean;
  refreshing: boolean;
  /** True when what is on screen came from the device, not the network. */
  fromCache: boolean;
  refresh: () => void;
}

export function useLoader<T>(
  load: (maxAgeMs: number) => Promise<Loaded<T>>,
  deps: unknown[],
): LoaderState<T> {
  const [data, setData] = useState<T | undefined>();
  const [error, setError] = useState<Error | undefined>();
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [fromCache, setFromCache] = useState(false);

  // A refresh that resolves after the screen has gone would set state on an
  // unmounted component, which React warns about and which can also overwrite
  // whatever the next screen loaded.
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const run = useCallback(
    async (maxAgeMs: number) => {
      try {
        const result = await load(maxAgeMs);
        if (!alive.current) return;
        setData(result.data);
        setFromCache(result.fromCache);
        setError(undefined);
      } catch (caught) {
        if (!alive.current) return;
        setError(caught instanceof Error ? caught : new Error(String(caught)));
      } finally {
        if (alive.current) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    deps,
  );

  useEffect(() => {
    setLoading(true);
    run(REFRESH_AFTER_MS);
  }, [run]);

  const refresh = useCallback(() => {
    setRefreshing(true);
    // maxAge 0: an explicit pull means the reader wants the network asked,
    // not the cache re-read.
    run(0);
  }, [run]);

  return { data, error, loading, refreshing, fromCache, refresh };
}
