/**
 * How each signal reads across the whole market.
 *
 * A company page shows fourteen verdicts and gives no way to tell a demanding
 * signal from a generous one: six BUYs means something very different if the
 * average company gets two than if it gets ten. Stated as shares of the whole
 * screened universe, the signals that discriminate separate from the ones that
 * pass almost everybody -- which is both the useful reading and, where a signal
 * is near-unanimous, the first place to look for a modelling error.
 *
 * Shares rather than counts, because every row has the same denominator and
 * what matters is the mix. Sorted by BUY share so the two ends of the chart are
 * the strict signals and the permissive ones.
 */

import type { SignalClimate as Row } from '../types';
import { Chart } from './Chart';

interface Props {
  signals: Row[];
  height?: number;
}

const VERDICTS = [
  ['BUY', 'oklch(0.72 0.16 155)'],
  ['HOLD', 'oklch(0.60 0.015 260)'],
  ['SELL', 'oklch(0.65 0.19 25)'],
  ['UNKNOWN', 'oklch(0.34 0.014 260)'],
] as const;

export function SignalClimate({ signals, height = 420 }: Props) {
  // Reversed because an ECharts category axis draws its first entry at the
  // bottom; without this the sort reads upside down.
  const rows = [...signals]
    .map((s) => ({ ...s, total: s.BUY + s.HOLD + s.SELL + s.UNKNOWN }))
    .filter((s) => s.total > 0)
    .sort((a, b) => a.BUY / a.total - b.BUY / b.total);

  if (!rows.length) {
    return (
      <p className="panel p-4 text-sm text-ink-400">
        Signal aggregates arrive with the next publish.
      </p>
    );
  }

  const option = {
    // Left gutter sized to the longest published label -- "EV/EBITDA upside %
    // (net debt)", 29 characters -- rather than to a round number, which
    // truncated it to "EV/EBITDA upside % (net ..." and hid which of the two
    // EV/EBITDA signals the row was.
    grid: { left: 176, right: 56, top: 8, bottom: 28 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params: { dataIndex: number; seriesName: string }[]) => {
        const row = rows[params[0]?.dataIndex ?? 0];
        if (!row) return '';
        const lines = VERDICTS.map(([v]) => {
          const n = row[v];
          return `${v.toLowerCase()} ${n.toLocaleString('en-IN')}`
            + ` (${((n / row.total) * 100).toFixed(1)}%)`;
        });
        return `<b>${row.label}</b><br/>${lines.join('<br/>')}`;
      },
    },
    legend: { show: false },
    xAxis: {
      type: 'value',
      max: 100,
      axisLabel: {
        color: 'oklch(0.52 0.015 260)',
        fontSize: 10,
        formatter: '{value}%',
      },
      splitLine: { lineStyle: { color: 'oklch(0.26 0.013 260)' } },
    },
    yAxis: {
      type: 'category',
      data: rows.map((r) => r.label),
      axisLabel: { color: 'oklch(0.70 0.012 260)', fontSize: 10, width: 166, overflow: 'truncate' },
      axisLine: { lineStyle: { color: 'oklch(0.32 0.014 260)' } },
      axisTick: { show: false },
    },
    series: VERDICTS.map(([verdict, color], i) => ({
      name: verdict,
      type: 'bar',
      stack: 'verdict',
      data: rows.map((r) => (r[verdict] / r.total) * 100),
      itemStyle: { color },
      barMaxWidth: 18,
      // Only the BUY share is labelled. Four labels per bar on fourteen bars
      // is 56 numbers for a chart whose point is the comparison between rows.
      label: i === 0
        ? {
          show: true,
          position: 'right',
          distance: -46,
          color: 'oklch(0.97 0.004 260)',
          fontSize: 10,
          formatter: (p: { value: number }) => `${p.value.toFixed(0)}%`,
        }
        : { show: false },
    })),
  };

  return (
    <div>
      <Chart option={option} style={{ height }} />
      <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-600">
        {VERDICTS.map(([verdict, color]) => (
          <span key={verdict} className="inline-flex items-center gap-1">
            <span
              className="inline-block h-2 w-2 rounded-sm"
              style={{ background: color }}
            />
            {verdict.toLowerCase()}
          </span>
        ))}
        <span>· labelled share is BUY · bars are the whole screened universe</span>
      </p>
    </div>
  );
}
