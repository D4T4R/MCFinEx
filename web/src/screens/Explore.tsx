/**
 * The cross-section: quality against price, sized by what the company is worth.
 *
 * This is the view the table cannot give. A screener sorted by upside answers
 * "which is cheapest"; a scatter answers "is this cheap because it is good value
 * or because it is bad", which is the question the signals are proxies for.
 *
 * Drawn over whatever the filters leave, so narrowing the screen narrows the
 * cloud -- the two views are the same selection seen from different directions.
 */

import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';

import { Chart } from '../components/Chart';
import { crore, percent, plain } from '../lib/format';
import { robustBounds } from '../lib/scale';
import type { Company, Tier } from '../types';

/** Matches the tier chips, so the legend needs no learning. */
const TIER_COLOUR: Record<Tier, string> = {
  'High conviction': '#4ade80',
  'Below entry price': '#60a5fa',
  'Re-rating': '#fbbf24',
  Watch: '#94a3b8',
  None: '#475569',
};

const AXES = [
  { key: 'stock_pe', label: 'P/E', hint: 'Lower is cheaper. Loss-making companies have none and are not plotted.' },
  { key: 'roce', label: 'ROCE %', hint: 'Return on capital employed. Higher is a better business.' },
  { key: 'upside_pct', label: 'Upside %', hint: 'To the EV/EBITDA target.' },
  { key: 'price_to_book', label: 'Price / book', hint: 'Lower is cheaper against net assets.' },
  { key: 'dividend_yield', label: 'Dividend yield %', hint: '' },
  { key: 'promoter_holding', label: 'Promoter holding %', hint: '' },
] as const;

type AxisKey = (typeof AXES)[number]['key'];

interface Props {
  companies: Company[];
  x: AxisKey;
  y: AxisKey;
  onAxes: (axes: { x: AxisKey; y: AxisKey }) => void;
}

export function Explore({ companies, x, y, onAxes }: Props) {
  const navigate = useNavigate();

  // Only companies with both measures. A point placed at zero for a missing P/E
  // would sit in the cheapest corner and read as the most attractive thing on
  // the chart.
  const plotted = useMemo(
    () => companies.filter((c) => c[x] != null && c[y] != null),
    [companies, x, y],
  );
  const dropped = companies.length - plotted.length;

  const byTier = useMemo(() => {
    const groups = new Map<Tier, Company[]>();
    for (const company of plotted) {
      const list = groups.get(company.tier);
      if (list) list.push(company);
      else groups.set(company.tier, [company]);
    }
    return groups;
  }, [plotted]);

  const xLabel = AXES.find((a) => a.key === x)?.label ?? x;
  const yLabel = AXES.find((a) => a.key === y)?.label ?? y;

  // Drawn over the middle of the distribution. Scaled to the extremes these
  // axes are unreadable: P/E reaches 31,244 against a median of 29.8.
  const xBounds = useMemo(
    () => robustBounds(plotted.map((c) => c[x] as number)), [plotted, x]);
  const yBounds = useMemo(
    () => robustBounds(plotted.map((c) => c[y] as number)), [plotted, y]);
  const offScale = useMemo(
    () => plotted.filter((c) => {
      const cx = c[x] as number, cy = c[y] as number;
      return (xBounds && (cx < xBounds.min || cx > xBounds.max))
          || (yBounds && (cy < yBounds.min || cy > yBounds.max));
    }).length,
    [plotted, x, y, xBounds, yBounds],
  );

  const option = useMemo(
    () => ({
      backgroundColor: 'transparent',
      grid: { left: 64, right: 24, top: 24, bottom: 56 },
      tooltip: {
        trigger: 'item',
        backgroundColor: '#1e293b',
        borderColor: '#334155',
        textStyle: { color: '#e2e8f0', fontSize: 12 },
        formatter: (p: { data: { company: Company } }) => {
          const c = p.data.company;
          return [
            `<strong>${c.ticker}</strong> ${c.name ?? ''}`,
            `${c.sector ?? '—'} · ${crore(c.market_cap)}`,
            `${xLabel}: ${plain(c[x] as number | null, 2)}`,
            `${yLabel}: ${plain(c[y] as number | null, 2)}`,
            `${c.buy_signals}/${c.scored} BUY · upside ${percent(c.upside_pct, 0)}`,
          ].join('<br/>');
        },
      },
      legend: {
        bottom: 0,
        textStyle: { color: '#94a3b8', fontSize: 11 },
        inactiveColor: '#475569',
      },
      xAxis: {
        min: xBounds?.min, max: xBounds?.max,
        name: xLabel, nameLocation: 'middle', nameGap: 28,
        nameTextStyle: { color: '#64748b', fontSize: 11 },
        splitLine: { lineStyle: { color: '#1e293b' } },
        axisLine: { lineStyle: { color: '#334155' } },
        axisLabel: { color: '#64748b', fontSize: 11 },
      },
      yAxis: {
        min: yBounds?.min, max: yBounds?.max,
        name: yLabel, nameLocation: 'middle', nameGap: 44,
        nameTextStyle: { color: '#64748b', fontSize: 11 },
        splitLine: { lineStyle: { color: '#1e293b' } },
        axisLine: { lineStyle: { color: '#334155' } },
        axisLabel: { color: '#64748b', fontSize: 11 },
      },
      // Brushing zooms rather than filters: the table is the place to narrow the
      // set, and having two controls that both change "what is selected" in
      // different ways is how a reader loses track of what they are looking at.
      dataZoom: [
        { type: 'inside', xAxisIndex: 0 }, { type: 'inside', yAxisIndex: 0 },
      ],
      series: [...byTier.entries()].map(([tier, group]) => ({
        name: tier,
        type: 'scatter',
        large: true,
        largeThreshold: 400,
        symbolSize: (datum: { cap: number }) =>
          // Area, not radius, proportional to market cap: scaling the radius
          // makes a company ten times larger look a hundred times bigger.
          Math.max(4, Math.min(34, Math.sqrt(Math.sqrt(datum.cap ?? 1)) * 1.6)),
        itemStyle: { color: TIER_COLOUR[tier], opacity: 0.72 },
        data: group.map((company) => ({
          value: [company[x] as number, company[y] as number],
          cap: company.market_cap ?? 1,
          company,
        })),
      })),
    }),
    [byTier, x, y, xLabel, yLabel, xBounds, yBounds],
  );

  return (
    <div className="panel p-4">
      <div className="mb-4 flex flex-wrap items-end gap-6">
        <label className="flex flex-col gap-1">
          <span className="text-[11px] font-medium uppercase tracking-wide text-ink-600">
            Horizontal
          </span>
          <select
            value={x}
            onChange={(e) => onAxes({ x: e.target.value as AxisKey, y })}
            className="rounded-md border border-ink-800 bg-ink-900 px-2 py-1 text-sm text-ink-100 focus:border-accent focus:outline-none"
          >
            {AXES.map((a) => (
              <option key={a.key} value={a.key}>{a.label}</option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1">
          <span className="text-[11px] font-medium uppercase tracking-wide text-ink-600">
            Vertical
          </span>
          <select
            value={y}
            onChange={(e) => onAxes({ x, y: e.target.value as AxisKey })}
            className="rounded-md border border-ink-800 bg-ink-900 px-2 py-1 text-sm text-ink-100 focus:border-accent focus:outline-none"
          >
            {AXES.map((a) => (
              <option key={a.key} value={a.key}>{a.label}</option>
            ))}
          </select>
        </label>

        <p className="max-w-md text-xs text-ink-600">
          Bubble area is market capitalisation. Scroll to zoom, click a point to
          open the company.
          {dropped > 0 && (
            <>
              {' '}
              <span className="text-hold/80">
                {dropped.toLocaleString('en-IN')} of {companies.length.toLocaleString('en-IN')}
              </span>{' '}
              are not plotted: one of the two measures is unpublished for them.
            </>
          )}
          {offScale > 0 && (
            <>
              {' '}The axes cover the middle of the distribution, so{' '}
              <span className="text-hold/80">{offScale.toLocaleString('en-IN')}</span>{' '}
              more sit outside it. Scaled to the extremes the rest would be a
              single column of dots.
            </>
          )}
        </p>
      </div>

      <Chart
        option={option}
        style={{ height: 'calc(100vh - 24rem)', minHeight: 420 }}
        onEvents={{
          click: (event: { data?: { company?: Company } }) => {
            const id = event.data?.company?.id;
            if (id) navigate(`/company/${id}`);
          },
        }}
      />
    </div>
  );
}
