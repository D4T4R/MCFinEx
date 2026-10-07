/**
 * One fetch, one filter state, four views of it.
 *
 * The payload is loaded once here rather than per screen: it is a single static
 * file and every view reads the same cross-section, so fetching per route would
 * make switching tabs cost a round trip and let two tabs disagree about which
 * night they are showing.
 */

import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom';

import { FilterBar } from './components/FilterBar';
import { Shell } from './components/Shell';
import { loadScreen, UnsupportedSchemaError } from './data/remote';
import { apply, EMPTY_FILTERS, sectorsOf, type Filters } from './lib/filters';
import { supabase } from './lib/supabase';
import { useWatchlist } from './lib/useWatchlist';
import { Screener } from './screens/Screener';
import type { ScreenPayload } from './types';

// The three screens that draw charts are split out. ECharts is 352 KB over the
// wire even tree-shaken, and the table -- which is where a reader lands and
// mostly stays -- plots nothing. Loading it on arrival delays the only thing
// they asked for.
const Explore = lazy(() =>
  import('./screens/Explore').then((m) => ({ default: m.Explore })));
const Sectors = lazy(() =>
  import('./screens/Sectors').then((m) => ({ default: m.Sectors })));
const CompanyPage = lazy(() =>
  import('./screens/CompanyPage').then((m) => ({ default: m.CompanyPage })));

function Loading() {
  return <div className="panel p-10 text-center text-sm text-ink-600">Loading…</div>;
}

type AxisKey = 'stock_pe' | 'roce' | 'upside_pct' | 'price_to_book'
  | 'dividend_yield' | 'promoter_holding';

export default function App() {
  const [payload, setPayload] = useState<ScreenPayload | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [axes, setAxes] = useState<{ x: AxisKey; y: AxisKey }>({
    // Opens on the question the screen exists to answer: is it cheap because it
    // is good, or cheap because it is not.
    x: 'stock_pe',
    y: 'roce',
  });
  const [userId, setUserId] = useState<string | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    loadScreen()
      .then(setPayload)
      .catch((e) => setError(e instanceof Error ? e : new Error(String(e))));
  }, []);

  useEffect(() => {
    if (!supabase) return;
    void supabase.auth.getSession().then(({ data }) => {
      setUserId(data.session?.user.id ?? null);
      setEmail(data.session?.user.email ?? null);
    });
    const { data } = supabase.auth.onAuthStateChange((_event, session) => {
      setUserId(session?.user.id ?? null);
      setEmail(session?.user.email ?? null);
    });
    return () => data.subscription.unsubscribe();
  }, []);

  const watchlist = useWatchlist(userId);
  const companies = payload?.companies ?? [];
  const sectors = useMemo(() => sectorsOf(companies), [companies]);
  // The company page reads its summary from here rather than fetching again:
  // every company is already in memory, including the 975 with no detail file.
  const byId = useMemo(
    () => new Map(companies.map((c) => [c.id, c])), [companies]);
  const visible = useMemo(
    () => apply(companies, filters, watchlist.ids),
    [companies, filters, watchlist.ids],
  );

  const pickSector = useCallback(
    (sector: string) => {
      setFilters((current) => ({ ...current, sectors: [sector] }));
      navigate('/');
    },
    [navigate],
  );

  if (error) {
    const stale = error instanceof UnsupportedSchemaError;
    return (
      <Shell payload={null} email={null}>
        <div className="panel p-8 text-center">
          <h1 className="text-sm font-medium text-ink-100">
            {stale ? 'This page is out of date' : 'The screen could not be loaded'}
          </h1>
          <p className="mx-auto mt-2 max-w-lg text-sm text-ink-400">{error.message}</p>
          {!stale && (
            <p className="mx-auto mt-2 max-w-lg text-xs text-ink-600">
              The data is published by a nightly job. If this persists, that job
              has not run rather than nothing having qualified.
            </p>
          )}
        </div>
      </Shell>
    );
  }

  if (!payload) {
    return (
      <Shell payload={null} email={email}>
        <div className="panel p-10 text-center text-sm text-ink-600">
          Loading the screen…
        </div>
      </Shell>
    );
  }

  const bar = (
    <FilterBar
      filters={filters}
      onChange={setFilters}
      sectors={sectors}
      showing={visible.length}
      total={payload.universe}
      signedIn={Boolean(userId)}
    />
  );

  return (
    <Shell payload={payload} email={email}>
      <Routes>
        <Route
          path="/"
          element={
            <>
              {bar}
              <Screener
                companies={visible}
                watched={watchlist.ids}
                onToggleWatch={watchlist.toggle}
              />
            </>
          }
        />
        <Route
          path="/explore"
          element={
            <>
              {bar}
              <Suspense fallback={<Loading />}>
                <Explore companies={visible} x={axes.x} y={axes.y} onAxes={setAxes} />
              </Suspense>
            </>
          }
        />
        <Route
          path="/sectors"
          element={
            <Suspense fallback={<Loading />}>
              <Sectors sectors={payload.sectors} onPick={pickSector} />
            </Suspense>
          }
        />
        <Route
          path="/company/:id"
          element={
            <Suspense fallback={<Loading />}>
              <CompanyPage byId={byId} />
            </Suspense>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  );
}
