/**
 * The cross-section: quality against price, sized by what the company is worth.
 *
 * This is the view the table cannot give. A screener sorted by upside answers
 * "which is cheapest"; a scatter answers "is this cheap because it is good value
 * or because it is bad", which is the question the signals are proxies for.
 *
 * Three things here are about reading it honestly rather than about decoration.
 *
 * **Log axes where the measure is log-normal.** P/E, price-to-book and market
 * capitalisation span orders of magnitude, and on a linear axis 77% of the
 * 2,317 plotted companies landed in the leftmost tenth -- an unreadable smear
 * against nine-tenths of empty canvas. In log space the same points spread
 * across seven deciles with 20% in the busiest. The measures that are not
 * log-normal -- ROCE, upside, promoter holding, which all go negative or sit on
 * a bounded range -- stay linear, because a log axis there would drop every
 * negative value without saying so.
 *
 * **Weight by what the reader is looking for.** A third of the universe reached
 * no tier. Drawn at the same weight as everything else they are most of the ink
 * and none of the signal, so they recede to a faint ground and the shortlists
 * sit on top of them.
 *
 * **Median crosshairs.** The useful question is not where one company sits but
 * which quadrant it is in -- cheap and good, or cheap and bad. The medians are
 * drawn and the quadrant a reader is most likely to want is named.
 */

import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';

import { Chart } from '../components/Chart';
import { crore, percent, plain } from '../lib/format';
import { robustBounds } from '../lib/scale';
import type { Company, Tier } from '../types';

/**
 * Brighter than the chips, and ordered by how much attention each deserves.
 *
 * `None` is deliberately close to the background: it is 32% of the companies
 * and none of the answer, so it should read as texture rather than as data.
 */
const TIER_STYLE: Record<Tier, { colour: string; opacity: number; scale: number }> = {
  'High conviction': { colour: '#34d399', opacity: 0.95, scale: 1.35 },
  'Below entry price': { colour: '#38bdf8', opacity: 0.8, scale: 1.0 },
  'Re-rating': { colour: '#fbbf24', opacity: 0.85, scale: 1.0 },
  Watch: { colour: '#a78bfa', opacity: 0.7, scale: 0.9 },
  None: { colour: '#334155', opacity: 0.45, scale: 0.6 },
};

/** Drawn last sits on top. Worst first, so the shortlists are never buried. */
const DRAW_ORDER: Tier[] = [
  'None', 'Watch', 'Re-rating', 'Below entry price', 'High conviction',
];

interface Axis {
  key: AxisKey;
  label: string;
  /** Log only where the quantity is log-normal and strictly positive. */
  scale: 'linear' | 'log';
  /** Which direction is attractive, for naming the quadrant. */
  better: 'lower' | 'higher';
  hint: string;
}

export type AxisKey =
  | 'stock_pe' | 'roce' | 'upside_pct' | 'price_to_book'
  | 'dividend_yield' | 'promoter_holding' | 'market_cap';

const AXES: Axis[] = [
  { key: 'stock_pe', label: 'P/E', scale: 'log', better: 'lower',
    hint: 'Log scale: P/E spans two decades. Loss-making companies have none and are not plotted.' },
  { key: 'roce', label: 'ROCE %', scale: 'linear', better: 'higher',
    hint: 'Return on capital employed. Goes negative, so it stays linear.' },
  { key: 'price_to_book', label: 'Price / book', scale: 'log', better: 'lower',
    hint: 'Log scale. Companies with no or negative book value are not plotted.' },
  { key: 'market_cap', label: 'Market cap (₹ cr)', scale: 'log', better: 'higher',
    hint: 'Log scale: the universe runs from a few crore to fourteen lakh crore.' },
  { key: 'upside_pct', label: 'Upside %', scale: 'linear', better: 'higher',
    hint: 'To the EV/EBITDA target. Goes negative, so it stays linear.' },
  { key: 'dividend_yield', label: 'Dividend yield %', scale: 'linear', better: 'higher',
    hint: 'Many companies pay nothing, so a log axis would drop them.' },
  { key: 'promoter_holding', label: 'Promoter holding %', scale: 'linear', better: 'higher',
    hint: 'Bounded 0 to 100.' },
];

const axisOf = (key: AxisKey) => AXES.find((a) => a.key === key) as Axis;

/** Median of the plotted values, for the crosshair. */
function median(values: number[]): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)] ?? null;
}

interface Props {
  companies: Company[];
  x: AxisKey;
  y: AxisKey;
  onAxes: (axes: { x: AxisKey; y: AxisKey }) => void;
}

export function Explore({ companies, x, y, onAxes }: Props) {
  const navigate = useNavigate();
  const ax = axisOf(x);
  const ay = axisOf(y);

  // A point needs both measures, and a log axis needs them positive. Dropping a
  // company silently would make the cloud look like the whole market, so the
  // count is reported underneath.
  const plotted = useMemo(
    () => companies.filter((c) => {
      const cx = c[x], cy = c[y];
      if (cx == null || cy == null) return false;
      if (ax.scale === 'log' && cx <= 0) return false;
      if (ay.scale === 'log' && cy <= 0) return false;
      return true;
    }),
    [companies, x, y, ax.scale, ay.scale],
  );
  const dropped = companies.length - plotted.length;

  const xBounds = useMemo(
    () => robustBounds(plotted.map((c) => c[x] as number), 0.02, 0.98, ax.scale === 'log'),
    [plotted, x, ax.scale]);
  const yBounds = useMemo(
    () => robustBounds(plotted.map((c) => c[y] as number), 0.02, 0.98, ay.scale === 'log'),
    [plotted, y, ay.scale]);

  const xMedian = useMemo(() => median(plotted.map((c) => c[x] as number)), [plotted, x]);
  const yMedian = useMemo(() => median(plotted.map((c) => c[y] as number)), [plotted, y]);

  const offScale = useMemo(
    () => plotted.filter((c) => {
      const cx = c[x] as number, cy = c[y] as number;
      return (xBounds && (cx < xBounds.min || cx > xBounds.max))
          || (yBounds && (cy < yBounds.min || cy > yBounds.max));
    }).length,
    [plotted, x, y, xBounds, yBounds]);

  const byTier = useMemo(() => {
    const groups = new Map<Tier, Company[]>();
    for (const company of plotted) {
      const list = groups.get(company.tier);
      if (list) list.push(company);
      else groups.set(company.tier, [company]);
    }
    return groups;
  }, [plotted]);

  const option = useMemo(() => {
    const axisBase = (label: string, scale: 'linear' | 'log', bounds: typeof xBounds) => ({
      // ECharts names a numeric axis "value"; there is no "linear". Passing the
      // domain word straight through leaves it unable to build the coordinate
      // system, and it then renders an empty container with no error at all --
      // which reads as a layout bug rather than a bad option.
      type: scale === 'log' ? 'log' : 'value',
      min: bounds?.min, max: bounds?.max,
      nameTextStyle: { color: '#7c8da6', fontSize: 11 },
      splitLine: { lineStyle: { color: 'rgba(148,163,184,0.07)' } },
      axisLine: { lineStyle: { color: 'rgba(148,163,184,0.25)' } },
      axisTick: { show: false },
      axisLabel: { color: '#7c8da6', fontSize: 11, hideOverlap: true },
      name: label,
    });

    return {
      backgroundColor: 'transparent',
      animationDuration: 420,
      grid: { left: 70, right: 28, top: 28, bottom: 64 },
      tooltip: {
        trigger: 'item',
        backgroundColor: 'rgba(15,23,42,0.96)',
        borderColor: 'rgba(148,163,184,0.3)',
        padding: 10,
        textStyle: { color: '#e2e8f0', fontSize: 12 },
        formatter: (p: { data: { company: Company } }) => {
          const c = p.data.company;
          return [
            `<strong style="font-size:13px">${c.ticker}</strong> ` +
              `<span style="color:#94a3b8">${c.name ?? ''}</span>`,
            `<span style="color:#94a3b8">${c.sector ?? '—'} · ${crore(c.market_cap)}</span>`,
            `${ax.label}: <strong>${plain(c[x] as number | null, 2)}</strong>`,
            `${ay.label}: <strong>${plain(c[y] as number | null, 2)}</strong>`,
            `${c.buy_signals}/${c.scored} BUY · upside ${percent(c.upside_pct, 0)}`,
          ].join('<br/>');
        },
      },
      legend: {
        bottom: 2,
        itemGap: 18,
        icon: 'circle',
        textStyle: { color: '#94a3b8', fontSize: 11 },
        inactiveColor: '#334155',
      },
      xAxis: { ...axisBase(ax.label, ax.scale, xBounds), nameLocation: 'middle', nameGap: 30 },
      yAxis: { ...axisBase(ay.label, ay.scale, yBounds), nameLocation: 'middle', nameGap: 48 },
      dataZoom: [
        { type: 'inside', xAxisIndex: 0 }, { type: 'inside', yAxisIndex: 0 },
      ],
      series: DRAW_ORDER.filter((tier) => byTier.has(tier)).map((tier, index) => {
        const group = byTier.get(tier) as Company[];
        const style = TIER_STYLE[tier];
        return {
          name: tier,
          type: 'scatter',
          large: true,
          largeThreshold: 600,
          symbolSize: (datum: { cap: number }) =>
            // Area proportional to market cap, not radius: scaling the radius
            // makes a company ten times larger look a hundred times bigger.
            Math.max(3, Math.min(30, Math.sqrt(Math.sqrt(datum.cap ?? 1)) * 1.5)) * style.scale,
          itemStyle: {
            color: style.colour,
            opacity: style.opacity,
            borderColor: 'rgba(2,6,23,0.55)',
            borderWidth: 0.5,
          },
          emphasis: {
            scale: 1.6,
            itemStyle: {
              opacity: 1,
              borderColor: '#e2e8f0',
              borderWidth: 1.5,
              shadowBlur: 14,
              shadowColor: style.colour,
            },
          },
          // Dim the rest on hover: at 2,300 points the only way to see one is to
          // take the others out of the way.
          blur: { itemStyle: { opacity: Math.min(style.opacity, 0.18) } },
          z: index + 2,
          data: group.map((company) => ({
            value: [company[x] as number, company[y] as number],
            cap: company.market_cap ?? 1,
            company,
          })),
          // Crosshairs on the last-drawn series only, so they render once and
          // above the cloud.
          ...(index === DRAW_ORDER.filter((t) => byTier.has(t)).length - 1
            ? {
                markLine: {
                  silent: true,
                  symbol: 'none',
                  lineStyle: { color: 'rgba(226,232,240,0.28)', type: 'dashed', width: 1 },
                  label: {
                    color: '#7c8da6', fontSize: 10, formatter: '{b}',
                    position: 'insideEndTop',
                  },
                  data: [
                    ...(xMedian != null
                      ? [{ xAxis: xMedian, name: `median ${ax.label}` }] : []),
                    ...(yMedian != null
                      ? [{ yAxis: yMedian, name: `median ${ay.label}` }] : []),
                  ],
                },
              }
            : {}),
        };
      }),
    };
  }, [byTier, x, y, ax, ay, xBounds, yBounds, xMedian, yMedian]);

  // Which corner holds the companies that are attractive on both measures.
  const corner = `${ay.better === 'higher' ? 'top' : 'bottom'}-${ax.better === 'lower' ? 'left' : 'right'}`;

  return (
    <div className="panel p-4">
      <div className="mb-4 flex flex-wrap items-end gap-5">
        <AxisPicker label="Horizontal" value={x} onChange={(k) => onAxes({ x: k, y })} />
        <AxisPicker label="Vertical" value={y} onChange={(k) => onAxes({ x, y: k })} />

        <div className="max-w-xl space-y-1 text-xs text-ink-600">
          <p>
            Bubble area is market capitalisation. Dashed lines are the medians:
            the <span className="text-buy">{corner}</span> quadrant is low{' '}
            {ax.label} with high {ay.label}. Scroll to zoom, click to open.
          </p>
          <p>
            {(ax.scale === 'log' || ay.scale === 'log') && (
              <span className="text-accent/80">
                Log scale on {[ax.scale === 'log' && ax.label, ay.scale === 'log' && ay.label]
                  .filter(Boolean).join(' and ')}.{' '}
              </span>
            )}
            {dropped > 0 && (
              <>
                <span className="text-hold/80">
                  {dropped.toLocaleString('en-IN')} of {companies.length.toLocaleString('en-IN')}
                </span>{' '}
                not plotted — a measure is unpublished, zero or negative for them.{' '}
              </>
            )}
            {offScale > 0 && (
              <>
                The axes cover the middle of the distribution, so{' '}
                <span className="text-hold/80">{offScale.toLocaleString('en-IN')}</span>{' '}
                more sit outside it.
              </>
            )}
          </p>
        </div>
      </div>

      <Chart
        option={option}
        style={{ height: 'calc(100vh - 25rem)', minHeight: 440 }}
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

function AxisPicker({
  label, value, onChange,
}: {
  label: string;
  value: AxisKey;
  onChange: (key: AxisKey) => void;
}) {
  const axis = axisOf(value);
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] font-medium uppercase tracking-wide text-ink-600">
        {label}
      </span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as AxisKey)}
        title={axis.hint}
        className="rounded-md border border-ink-800 bg-ink-900 px-2 py-1 text-sm text-ink-100 focus:border-accent focus:outline-none"
      >
        {AXES.map((a) => (
          <option key={a.key} value={a.key}>
            {a.label}{a.scale === 'log' ? ' (log)' : ''}
          </option>
        ))}
      </select>
    </label>
  );
}
