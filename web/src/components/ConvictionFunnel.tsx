/**
 * How the universe narrows to a shortlist.
 *
 * The tier counts are already on the screener as five independent numbers.
 * Nothing there shows that each is a subset of the one before, which is the
 * only thing that makes "161 high conviction" mean anything: a reader cannot
 * tell selectivity from scarcity without the denominator.
 */

import { funnel } from '../lib/shapes';
import { Chart } from './Chart';

interface Props {
  tiers: Record<string, number>;
  universe: number;
  height?: number;
}

export function ConvictionFunnel({ tiers, universe, height = 300 }: Props) {
  const stages = funnel(tiers, universe);
  if (!universe) {
    return <p className="panel p-4 text-sm text-ink-400">Nothing screened yet.</p>;
  }

  // Width is the share of the *universe*, not of the stage above, so the
  // narrowing is drawn to scale. Sorted off, because the stages are already
  // in their logical order and letting ECharts reorder them by size would
  // turn a sequence into a ranking.
  const option = {
    tooltip: {
      trigger: 'item',
      formatter: (p: { dataIndex: number }) => {
        const s = stages[p.dataIndex];
        if (!s) return '';
        return `<b>${s.label}</b><br/>${s.count.toLocaleString('en-IN')} companies`
          + `<br/>${s.ofPrevious.toFixed(1)}% of the stage above`
          + `<br/><span style="opacity:.7">${s.hint}</span>`;
      },
    },
    series: [{
      type: 'funnel',
      sort: 'none',
      gap: 3,
      left: '8%',
      right: '8%',
      top: 10,
      bottom: 10,
      minSize: '22%',
      data: stages.map((s, i) => ({
        name: s.label,
        value: s.count,
        itemStyle: {
          // Darkening with depth: the narrow end is the one being claimed,
          // and should read as the most concentrated rather than the least.
          color: [
            'oklch(0.42 0.06 255)', 'oklch(0.50 0.09 255)',
            'oklch(0.58 0.12 255)', 'oklch(0.68 0.15 255)',
          ][i],
          borderColor: 'oklch(0.16 0.01 260)',
          borderWidth: 1,
        },
      })),
      label: {
        position: 'inside',
        color: 'oklch(0.97 0.004 260)',
        fontSize: 11,
        formatter: (p: { dataIndex: number }) => {
          const s = stages[p.dataIndex];
          return s ? `${s.label}  ${s.count.toLocaleString('en-IN')}` : '';
        },
      },
    }],
  };

  return (
    <div>
      <Chart option={option} style={{ height }} />
      <p className="mt-1 text-xs text-ink-600">
        {stages[stages.length - 1]?.count.toLocaleString('en-IN')} of{' '}
        {universe.toLocaleString('en-IN')} reach high conviction
        {' '}({((stages[stages.length - 1]!.count / universe) * 100).toFixed(1)}%).
        Each band is a subset of the one above it.
      </p>
    </div>
  );
}
