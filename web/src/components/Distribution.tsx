/**
 * The shape a metric takes across the whole market.
 *
 * The scatter says where a company sits; this says what it sits in. They share
 * the same axis definition deliberately -- same metric list, same log setting
 * -- so a reader moving between them is never shown the same number on two
 * different scales.
 */

import { useMemo } from 'react';

import type { Company } from '../types';
import { plain } from '../lib/format';
import { robustBounds } from '../lib/scale';
import { distribution } from '../lib/shapes';
import { Chart } from './Chart';

interface Props {
  companies: Company[];
  metric: keyof Company & string;
  label: string;
  log: boolean;
  height?: number;
}

/** Axis labels on a log scale: 1,000 rather than 1000.0, 0.5 rather than 0.5000. */
function edge(value: number): string {
  if (value >= 1000) return Math.round(value).toLocaleString('en-IN');
  if (value >= 10) return String(Math.round(value));
  return plain(value, value >= 1 ? 1 : 2);
}

export function Distribution({ companies, metric, label, log, height = 260 }: Props) {
  const shape = useMemo(() => {
    const values = companies.map((c) => c[metric] as number | null);
    // The same window, the same percentiles and the same helper the scatter
    // uses, so the two charts never disagree about where the axis ends.
    // Computed from this metric alone rather than taken from the scatter's
    // bounds, which are narrowed by the *other* axis too -- the histogram
    // should not shift because a reader changed the y axis.
    const finite = values.filter(
      (v): v is number => typeof v === 'number' && Number.isFinite(v),
    );
    const bounds = robustBounds(finite, 0.02, 0.98, log);
    return distribution(values, { log, min: bounds?.min, max: bounds?.max });
  }, [companies, metric, log]);

  if (!shape.bins.length) {
    return (
      <p className="panel p-4 text-sm text-ink-400">
        No company carries a {label.toLowerCase()} to plot.
      </p>
    );
  }

  const marks = [
    { value: shape.p10, name: '10th' },
    { value: shape.median, name: 'median' },
    { value: shape.p90, name: '90th' },
  ].filter((m): m is { value: number; name: string } => m.value != null);

  // Bars are placed on a value axis rather than a category axis so the log
  // spacing is real: on a category axis every bin would be the same width on
  // screen, which is precisely the distortion the log binning exists to undo.
  const option = {
    grid: { left: 52, right: 16, top: 16, bottom: 34 },
    tooltip: {
      trigger: 'axis',
      formatter: (params: { data: [number, number] }[]) => {
        const point = params[0];
        if (!point) return '';
        const [centre, count] = point.data;
        const bin = shape.bins.find((b) => (b.from + b.to) / 2 === centre);
        const range = bin ? `${edge(bin.from)} – ${edge(bin.to)}` : edge(centre);
        const share = ((count / companies.length) * 100).toFixed(1);
        return `${range}<br/><b>${count}</b> companies · ${share}%`;
      },
    },
    xAxis: {
      // Not 'linear' -- ECharts names a numeric axis "value", and an unknown
      // type renders an empty container with no error at all.
      type: log ? 'log' : 'value',
      name: label,
      nameLocation: 'middle',
      nameGap: 24,
      nameTextStyle: { color: 'oklch(0.52 0.015 260)', fontSize: 11 },
      axisLabel: { color: 'oklch(0.52 0.015 260)', fontSize: 10, formatter: edge },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: 'oklch(0.52 0.015 260)', fontSize: 10 },
      splitLine: { lineStyle: { color: 'oklch(0.26 0.013 260)' } },
    },
    series: [{
      type: 'bar',
      data: shape.bins.map((b) => [(b.from + b.to) / 2, b.count]),
      barMaxWidth: 22,
      itemStyle: { color: 'oklch(0.62 0.13 255)', borderRadius: [2, 2, 0, 0] },
      markLine: {
        symbol: 'none',
        silent: true,
        label: {
          formatter: (p: { name: string }) => p.name,
          color: 'oklch(0.70 0.012 260)',
          fontSize: 10,
        },
        lineStyle: { color: 'oklch(0.52 0.015 260)', type: 'dashed' },
        data: marks.map((m) => ({ xAxis: m.value, name: m.name })),
      },
    }],
  };

  return (
    <div>
      <Chart option={option} style={{ height }} />
      <p className="mt-1 text-xs text-ink-600">
        {companies.length - shape.excluded - shape.clipped} companies plotted
        {shape.excluded > 0 && (
          // Never silently show a subset: on a log axis this is every
          // loss-making or negative-book company, which is not a rounding
          // error and changes what the shape means.
          <> · {shape.excluded} not plotted
            {log ? ' (zero or negative on a log scale)' : ' (no value)'}</>
        )}
        {shape.clipped > 0 && <> · {shape.clipped} beyond the axis</>}
        {shape.median != null && <> · median {edge(shape.median)}</>}
      </p>
    </div>
  );
}
