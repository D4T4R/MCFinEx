/**
 * Median upside against how much of the company its promoters still own.
 *
 * Four bars rather than a scatter, on purpose. The relationship is weak, and
 * 2,700 points invites a reader to trace a trend through noise; four bars
 * either differ or they do not. The median rather than the mean for the same
 * reason the Explore axes are clamped -- modelled upside reaches six figures
 * on a handful of near-zero-EBITDA companies, and one of those would set the
 * height of a bar describing four hundred businesses.
 */

import type { Company } from '../types';
import { holdingBands } from '../lib/shapes';
import { Chart } from './Chart';

interface Props {
  companies: Company[];
  height?: number;
}

export function HoldingBands({ companies, height = 260 }: Props) {
  const bands = holdingBands(companies);
  const plotted = bands.filter((b) => b.medianUpside != null);
  if (!plotted.length) {
    return (
      <p className="panel p-4 text-sm text-ink-400">
        No company carries both a promoter holding and a modelled upside.
      </p>
    );
  }

  const option = {
    grid: { left: 52, right: 16, top: 16, bottom: 46 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params: { dataIndex: number }[]) => {
        const band = bands[params[0]?.dataIndex ?? 0];
        if (!band) return '';
        return `<b>Promoters hold ${band.label}</b><br/>`
          + `${band.count.toLocaleString('en-IN')} companies<br/>`
          + `median upside ${band.medianUpside?.toFixed(1) ?? '—'}%`;
      },
    },
    xAxis: {
      type: 'category',
      data: bands.map((b) => b.label),
      name: 'Promoter holding',
      nameLocation: 'middle',
      nameGap: 28,
      nameTextStyle: { color: 'oklch(0.52 0.015 260)', fontSize: 11 },
      axisLabel: { color: 'oklch(0.52 0.015 260)', fontSize: 10 },
      axisLine: { lineStyle: { color: 'oklch(0.32 0.014 260)' } },
    },
    yAxis: {
      type: 'value',
      name: 'Median upside %',
      nameTextStyle: { color: 'oklch(0.52 0.015 260)', fontSize: 11 },
      axisLabel: { color: 'oklch(0.52 0.015 260)', fontSize: 10 },
      splitLine: { lineStyle: { color: 'oklch(0.26 0.013 260)' } },
    },
    series: [{
      type: 'bar',
      // A band with no usable median is left null rather than drawn at zero:
      // a bar at the origin reads as "no upside" instead of "nothing to say".
      data: bands.map((b) => b.medianUpside),
      barMaxWidth: 64,
      itemStyle: { color: 'oklch(0.62 0.13 255)', borderRadius: [3, 3, 0, 0] },
      label: {
        show: true,
        position: 'top',
        color: 'oklch(0.70 0.012 260)',
        fontSize: 10,
        formatter: (p: { dataIndex: number }) => {
          const band = bands[p.dataIndex];
          return band?.count ? `n=${band.count.toLocaleString('en-IN')}` : '';
        },
      },
    }],
  };

  return (
    <div>
      <Chart option={option} style={{ height }} />
      <p className="mt-1 text-xs text-ink-600">
        Median, not mean — a few companies carry modelled upside in the tens of
        thousands of percent, and an average would let one of them set the bar.
      </p>
    </div>
  );
}
