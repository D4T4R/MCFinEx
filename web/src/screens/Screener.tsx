/**
 * The table. Every screened company, sortable, virtualised.
 *
 * Virtualised because the whole universe is 2,544 rows and the useful thing to
 * do with a screener is sort it and scroll -- paginating would hide the shape of
 * the distribution behind a page-size choice nobody asked to make.
 */

import { useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useVirtualizer } from '@tanstack/react-virtual';

import { SignalCount, TierChip } from '../components/Chips';
import { count, crore, money, percent, plain } from '../lib/format';
import type { Company } from '../types';

type SortKey =
  | 'ticker' | 'market_cap' | 'price' | 'upside_pct' | 'buy_signals'
  | 'sell_signals' | 'stock_pe' | 'roce' | 'dividend_yield'
  | 'price_to_book' | 'discount_to_entry_pct';

interface Column {
  key: SortKey | 'tier' | 'name';
  label: string;
  width: string;
  align?: 'right';
  sortable?: boolean;
  title?: string;
  render: (c: Company) => React.ReactNode;
}

const COLUMNS: Column[] = [
  {
    key: 'ticker', label: 'Ticker', width: 'w-28', sortable: true,
    render: (c) => (
      <Link to={`/company/${c.id}`} className="font-medium text-ink-50 hover:text-accent">
        {c.ticker}
      </Link>
    ),
  },
  {
    key: 'name', label: 'Company', width: 'min-w-[14rem] flex-1',
    render: (c) => <span className="truncate text-ink-300">{c.name ?? '—'}</span>,
  },
  {
    key: 'tier', label: 'Tier', width: 'w-40',
    render: (c) => <TierChip tier={c.tier} />,
  },
  {
    key: 'market_cap', label: 'Mkt cap', width: 'w-28', align: 'right', sortable: true,
    title: 'Rupees crore. Not scored -- size is not quality -- but it is the scale a figure should be read against.',
    render: (c) => <span className="tnum text-ink-300">{crore(c.market_cap)}</span>,
  },
  {
    key: 'price', label: 'Price', width: 'w-24', align: 'right', sortable: true,
    render: (c) => <span className="tnum text-ink-200">{money(c.price)}</span>,
  },
  {
    key: 'upside_pct', label: 'Upside', width: 'w-24', align: 'right', sortable: true,
    title: 'To the EV/EBITDA target.',
    render: (c) => (
      <span className={`tnum ${(c.upside_pct ?? 0) > 0 ? 'text-buy' : 'text-ink-400'}`}>
        {percent(c.upside_pct, 1)}
      </span>
    ),
  },
  {
    key: 'discount_to_entry_pct', label: 'To entry', width: 'w-24', align: 'right',
    sortable: true,
    title: 'How far below the 2/3 entry price it trades. Negative means it has run past it.',
    render: (c) => (
      <span className={`tnum ${(c.discount_to_entry_pct ?? 0) > 0 ? 'text-buy' : 'text-ink-600'}`}>
        {percent(c.discount_to_entry_pct, 0)}
      </span>
    ),
  },
  {
    key: 'buy_signals', label: 'BUY', width: 'w-20', align: 'right', sortable: true,
    render: (c) => <SignalCount buys={c.buy_signals} scored={c.scored} />,
  },
  {
    key: 'sell_signals', label: 'SELL', width: 'w-16', align: 'right', sortable: true,
    render: (c) => (
      <span className={`tnum ${c.sell_signals > 0 ? 'text-sell' : 'text-ink-600'}`}>
        {c.sell_signals}
      </span>
    ),
  },
  {
    key: 'stock_pe', label: 'P/E', width: 'w-20', align: 'right', sortable: true,
    render: (c) => <span className="tnum text-ink-300">{plain(c.stock_pe)}</span>,
  },
  {
    key: 'roce', label: 'ROCE', width: 'w-20', align: 'right', sortable: true,
    render: (c) => <span className="tnum text-ink-300">{plain(c.roce)}</span>,
  },
  {
    key: 'price_to_book', label: 'P/B', width: 'w-20', align: 'right', sortable: true,
    render: (c) => <span className="tnum text-ink-300">{plain(c.price_to_book, 2)}</span>,
  },
  {
    key: 'dividend_yield', label: 'Yield', width: 'w-20', align: 'right', sortable: true,
    render: (c) => <span className="tnum text-ink-300">{plain(c.dividend_yield, 2)}</span>,
  },
];

/**
 * Sort with the gaps at the bottom, whichever way the column is pointing.
 *
 * A null is not a small number. Sorting ascending with nulls treated as zero puts
 * every company with no P/E at the top of a "cheapest first" list, which is the
 * opposite of what was asked for and looks authoritative.
 */
function compare(a: Company, b: Company, key: SortKey, desc: boolean): number {
  const left = a[key] as number | string | null;
  const right = b[key] as number | string | null;
  if (left == null && right == null) return 0;
  if (left == null) return 1;
  if (right == null) return -1;
  if (typeof left === 'string' || typeof right === 'string') {
    return desc
      ? String(right).localeCompare(String(left))
      : String(left).localeCompare(String(right));
  }
  return desc ? right - left : left - right;
}

export function Screener({
  companies, watched, onToggleWatch,
}: {
  companies: Company[];
  watched: Set<string>;
  onToggleWatch: (id: string) => void;
}) {
  const [sortKey, setSortKey] = useState<SortKey>('buy_signals');
  const [desc, setDesc] = useState(true);
  const parentRef = useRef<HTMLDivElement>(null);

  const rows = useMemo(
    () => [...companies].sort((a, b) => compare(a, b, sortKey, desc)),
    [companies, sortKey, desc],
  );

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 40,
    overscan: 12,
  });

  const sortOn = (key: SortKey) => {
    if (key === sortKey) setDesc((d) => !d);
    else {
      setSortKey(key);
      setDesc(true);
    }
  };

  return (
    <div className="panel overflow-hidden">
     <div className="overflow-x-auto">
      <div className="flex min-w-[1180px] items-center gap-2 border-b border-ink-800 px-3 py-2 text-[11px] font-medium uppercase tracking-wide text-ink-600">
        <span className="w-7" />
        {COLUMNS.map((col) => {
          const active = col.sortable && col.key === sortKey;
          return (
            <button
              key={col.key}
              title={col.title}
              onClick={() => col.sortable && sortOn(col.key as SortKey)}
              className={`${col.width} ${col.align === 'right' ? 'text-right' : 'text-left'} ${
                col.sortable ? 'hover:text-ink-200' : 'cursor-default'
              } ${active ? 'text-ink-100' : ''}`}
            >
              {col.label}
              {active && <span className="ml-0.5">{desc ? '↓' : '↑'}</span>}
            </button>
          );
        })}
      </div>

      <div ref={parentRef} className="h-[calc(100vh-22rem)] min-h-[24rem] overflow-y-auto">
        {rows.length === 0 ? (
          <p className="p-8 text-center text-sm text-ink-600">
            Nothing matches these filters.
          </p>
        ) : (
          <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
            {virtualizer.getVirtualItems().map((item) => {
              const company = rows[item.index];
              if (!company) return null;
              const starred = watched.has(company.id);
              return (
                <div
                  key={company.id}
                  className="absolute left-0 flex w-full min-w-[1180px] items-center gap-2 border-b border-ink-900 px-3 text-sm hover:bg-ink-850/60"
                  style={{ height: item.size, top: item.start }}
                >
                  <button
                    onClick={() => onToggleWatch(company.id)}
                    aria-label={starred ? `Unwatch ${company.ticker}` : `Watch ${company.ticker}`}
                    className={`w-7 text-base leading-none ${
                      starred ? 'text-hold' : 'text-ink-800 hover:text-ink-600'
                    }`}
                  >
                    {starred ? '★' : '☆'}
                  </button>
                  {COLUMNS.map((col) => (
                    <div
                      key={col.key}
                      className={`${col.width} ${col.align === 'right' ? 'text-right' : ''} overflow-hidden`}
                    >
                      {col.render(company)}
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        )}
      </div>

     </div>

      <div className="border-t border-ink-800 px-3 py-1.5 text-[11px] text-ink-600">
        {count(rows.length)} rows · click a header to sort · ★ to watch
      </div>
    </div>
  );
}
