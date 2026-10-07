/**
 * The controls. Dense on purpose -- this is a screener, and a reader narrowing
 * 2,500 companies wants every lever in sight rather than behind a drawer.
 */

import { CAP_BANDS, type CapBand } from '../lib/format';
import { EMPTY_FILTERS, isDefault, type Filters } from '../lib/filters';
import { TIERS, TIER_HELP, type Tier } from '../types';
import { authConfigured } from '../lib/supabase';

interface Props {
  filters: Filters;
  onChange: (next: Filters) => void;
  sectors: string[];
  showing: number;
  total: number;
  signedIn: boolean;
}

function Toggle<T extends string>({
  options, selected, onToggle, titles,
}: {
  options: readonly T[];
  selected: T[];
  onToggle: (value: T) => void;
  titles?: Record<T, string>;
}) {
  return (
    <div className="flex flex-wrap gap-1">
      {options.map((option) => {
        const on = selected.includes(option);
        return (
          <button
            key={option}
            onClick={() => onToggle(option)}
            title={titles?.[option]}
            aria-pressed={on}
            className={`rounded-md px-2 py-1 text-xs ring-1 transition-colors ${
              on
                ? 'bg-accent/15 text-accent ring-accent/40'
                : 'bg-ink-900 text-ink-400 ring-ink-800 hover:text-ink-200'
            }`}
          >
            {option}
          </button>
        );
      })}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[11px] font-medium uppercase tracking-wide text-ink-600">
        {label}
      </span>
      {children}
    </div>
  );
}

function NumberInput({
  value, onChange, placeholder, suffix,
}: {
  value: number | null;
  onChange: (v: number | null) => void;
  placeholder: string;
  suffix?: string;
}) {
  return (
    <div className="flex items-center gap-1">
      <input
        type="number"
        value={value ?? ''}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
        className="tnum w-20 rounded-md border border-ink-800 bg-ink-900 px-2 py-1 text-xs text-ink-100 placeholder:text-ink-600 focus:border-accent focus:outline-none"
      />
      {suffix && <span className="text-xs text-ink-600">{suffix}</span>}
    </div>
  );
}

export function FilterBar({
  filters, onChange, sectors, showing, total, signedIn,
}: Props) {
  const set = <K extends keyof Filters>(key: K, value: Filters[K]) =>
    onChange({ ...filters, [key]: value });

  const toggleIn = <T,>(list: T[], value: T): T[] =>
    list.includes(value) ? list.filter((v) => v !== value) : [...list, value];

  return (
    <div className="panel mb-4 p-4">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <input
          value={filters.search}
          onChange={(e) => set('search', e.target.value)}
          placeholder="Search ticker or company"
          className="w-64 rounded-md border border-ink-800 bg-ink-900 px-3 py-1.5 text-sm text-ink-100 placeholder:text-ink-600 focus:border-accent focus:outline-none"
        />
        <p className="tnum text-sm text-ink-400">
          <span className="font-semibold text-ink-50">{showing.toLocaleString('en-IN')}</span>
          <span className="text-ink-600"> of {total.toLocaleString('en-IN')}</span>
        </p>
        {!isDefault(filters) && (
          <button
            onClick={() => onChange(EMPTY_FILTERS)}
            className="rounded-md px-2 py-1 text-xs text-ink-400 ring-1 ring-ink-800 hover:text-ink-200"
          >
            Clear filters
          </button>
        )}
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
        <Field label="Tier">
          <Toggle<Tier>
            options={TIERS}
            selected={filters.tiers}
            onToggle={(t) => set('tiers', toggleIn(filters.tiers, t))}
            titles={TIER_HELP}
          />
        </Field>

        <Field label="Market cap">
          <Toggle<CapBand>
            options={CAP_BANDS.map((b) => b.label)}
            selected={filters.capBands}
            onToggle={(b) => set('capBands', toggleIn(filters.capBands, b))}
          />
        </Field>

        <Field label={`Minimum BUY signals · ${filters.minBuys}`}>
          <input
            type="range" min={0} max={10} value={filters.minBuys}
            onChange={(e) => set('minBuys', Number(e.target.value))}
            className="accent-accent"
          />
        </Field>

        <Field label={`Maximum SELL signals · ${filters.maxSells}`}>
          <input
            type="range" min={0} max={10} value={filters.maxSells}
            onChange={(e) => set('maxSells', Number(e.target.value))}
            className="accent-accent"
          />
        </Field>

        <Field label="Thresholds">
          <div className="flex flex-wrap gap-2">
            <NumberInput value={filters.upsideMin} placeholder="Upside"
                         suffix="% min" onChange={(v) => set('upsideMin', v)} />
            <NumberInput value={filters.peMax} placeholder="P/E"
                         suffix="max" onChange={(v) => set('peMax', v)} />
            <NumberInput value={filters.roceMin} placeholder="ROCE"
                         suffix="% min" onChange={(v) => set('roceMin', v)} />
          </div>
        </Field>

        <Field label="Restrict to">
          <div className="flex flex-col gap-1.5 text-xs text-ink-400">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={filters.actionableOnly}
                     onChange={(e) => set('actionableOnly', e.target.checked)}
                     className="accent-accent" />
              At or below entry price
            </label>
            <label className="flex items-center gap-2" title="A company with no reported quarters as a listed entity cannot be corroborated: its per-share history spans the IPO.">
              <input type="checkbox" checked={filters.excludeNewListings}
                     onChange={(e) => set('excludeNewListings', e.target.checked)}
                     className="accent-accent" />
              Exclude new listings
            </label>
            <label className="flex items-center gap-2" title="EV/EBITDA is not meaningful for a bank or NBFC, so the headline model is skipped for them.">
              <input type="checkbox" checked={filters.excludeFinancials}
                     onChange={(e) => set('excludeFinancials', e.target.checked)}
                     className="accent-accent" />
              Exclude financials
            </label>
            {authConfigured && (
              <label
                className={`flex items-center gap-2 ${signedIn ? '' : 'text-ink-600'}`}
                title={signedIn ? '' : 'Your watchlist is kept in this browser; sign in to carry it between devices.'}
              >
                <input type="checkbox" checked={filters.watchedOnly}
                       onChange={(e) => set('watchedOnly', e.target.checked)}
                       className="accent-accent" />
                Watchlist only
              </label>
            )}
          </div>
        </Field>
      </div>

      {sectors.length > 0 && (
        <details className="mt-4">
          <summary className="cursor-pointer text-[11px] font-medium uppercase tracking-wide text-ink-600 hover:text-ink-400">
            Sector{filters.sectors.length ? ` · ${filters.sectors.length} selected` : ''}
          </summary>
          <div className="mt-2 flex max-h-40 flex-wrap gap-1 overflow-y-auto">
            <Toggle
              options={sectors}
              selected={filters.sectors}
              onToggle={(s) => set('sectors', toggleIn(filters.sectors, s))}
            />
          </div>
        </details>
      )}
    </div>
  );
}
