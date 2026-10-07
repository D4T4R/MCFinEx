/**
 * The frame: navigation, data provenance, sign-in, and the disclaimer.
 *
 * The as-of dates sit in the header rather than a footer because they qualify
 * every number on the page. Prices refresh nightly and fundamentals quarterly,
 * so they are reported separately -- collapsing them into one date would make
 * the older of the two invisible.
 */

import { NavLink } from 'react-router-dom';

import { humanDate } from '../lib/format';
import { authConfigured, signInWithGoogle, signOut } from '../lib/supabase';
import type { ScreenPayload } from '../types';

const TABS = [
  { to: '/', label: 'Screener' },
  { to: '/explore', label: 'Explore' },
  { to: '/sectors', label: 'Sectors' },
];

interface Props {
  payload: ScreenPayload | null;
  email: string | null;
  children: React.ReactNode;
}

export function Shell({ payload, email, children }: Props) {
  const priced = humanDate(payload?.price_date);
  const scraped = humanDate(payload?.last_scraped);

  return (
    <div className="min-h-screen bg-ink-950">
      <header className="sticky top-0 z-20 border-b border-ink-800 bg-ink-950/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1800px] flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <NavLink to="/" className="text-sm font-semibold tracking-tight text-ink-50">
            MCFinEx
          </NavLink>

          <nav className="flex items-center gap-1">
            {TABS.map((tab) => (
              <NavLink
                key={tab.to}
                to={tab.to}
                end={tab.to === '/'}
                className={({ isActive }) =>
                  `rounded-md px-3 py-1.5 text-sm transition-colors ${
                    isActive
                      ? 'bg-ink-850 text-ink-50'
                      : 'text-ink-400 hover:bg-ink-900 hover:text-ink-200'
                  }`
                }
              >
                {tab.label}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-4">
            {priced && (
              <p className="hidden text-xs text-ink-600 sm:block">
                Prices {priced}
                {scraped && <> · fundamentals to {scraped}</>}
              </p>
            )}
            {authConfigured && (
              email ? (
                <button
                  onClick={() => void signOut()}
                  className="rounded-md px-2.5 py-1.5 text-xs text-ink-400 hover:bg-ink-900 hover:text-ink-200"
                  title={email}
                >
                  Sign out
                </button>
              ) : (
                <button
                  onClick={() => void signInWithGoogle()}
                  className="rounded-md bg-ink-850 px-3 py-1.5 text-xs text-ink-100 ring-1 ring-ink-600 hover:bg-ink-800"
                >
                  Sign in
                </button>
              )
            )}
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1800px] px-4 py-5">{children}</main>

      <footer className="mx-auto max-w-[1800px] px-4 pb-10 pt-4">
        {/* Not small print. The screen is mechanical and this is the only place
            that says so in full. */}
        <p className="max-w-4xl text-xs leading-relaxed text-ink-600">
          {payload?.disclaimer ??
            'Signals are generated mechanically from published financials and are subjective. Do your own research before acting on anything here.'}
        </p>
      </footer>
    </div>
  );
}
