/**
 * Where the shortlist is concentrated.
 *
 * Area is how many companies a sector holds; colour is the share of them that
 * reached a tier. Those are deliberately different measures: a sector with two
 * picks out of three is interesting in a way a sector with twenty out of six
 * hundred is not, and a single-measure treemap hides whichever one it is not
 * drawing.
 */

import { useMemo } from 'react';

import { Chart } from '../components/Chart';
import { percent, plain } from '../lib/format';
import type { SectorHeat } from '../types';

export function Sectors({
  sectors, onPick,
}: {
  sectors: SectorHeat[];
  onPick: (sector: string) => void;
}) {
  const option = useMemo(
    () => ({
      backgroundColor: 'transparent',
      tooltip: {
        backgroundColor: '#1e293b',
        borderColor: '#334155',
        textStyle: { color: '#e2e8f0', fontSize: 12 },
        formatter: (p: { data: { heat?: SectorHeat } }) => {
          const h = p.data.heat;
          if (!h) return '';
          return [
            `<strong>${h.sector}</strong>`,
            `${h.picks} of ${h.total} reached a tier (${plain(h.share_pct, 0)}%)`,
            `median upside ${percent(h.median_upside_pct, 0)}`,
          ].join('<br/>');
        },
      },
      visualMap: {
        min: 0,
        max: Math.max(...sectors.map((s) => s.share_pct), 1),
        bottom: 0,
        left: 'center',
        orient: 'horizontal',
        text: ['More of the sector qualifies', 'Less'],
        textStyle: { color: '#94a3b8', fontSize: 11 },
        calculable: false,
        inRange: { color: ['#1e293b', '#1d4ed8', '#4ade80'] },
      },
      series: [
        {
          type: 'treemap',
          roam: false,
          nodeClick: false,
          breadcrumb: { show: false },
          top: 8, bottom: 56, left: 8, right: 8,
          itemStyle: { borderColor: '#0f172a', borderWidth: 2, gapWidth: 2 },
          label: {
            color: '#e2e8f0',
            fontSize: 11,
            overflow: 'truncate',
            formatter: (p: { data: { heat?: SectorHeat } }) =>
              p.data.heat
                ? `${p.data.heat.sector}\n${p.data.heat.picks}/${p.data.heat.total}`
                : '',
          },
          data: sectors.map((heat) => ({
            name: heat.sector,
            value: heat.total,
            heat,
            // Colour by qualifying share, size by how many companies there are.
            visualMapValue: heat.share_pct,
          })),
        },
      ],
    }),
    [sectors],
  );

  return (
    <div className="space-y-4">
      <div className="panel p-4">
        <p className="mb-3 max-w-2xl text-xs text-ink-600">
          Tile size is how many companies the sector holds; colour is the share of
          them that reached a shortlist. Click a tile to filter the screener to it.
        </p>
        <Chart
          option={option}
          style={{ height: 'calc(100vh - 26rem)', minHeight: 380 }}
          onEvents={{
            click: (event: { data?: { heat?: SectorHeat } }) => {
              const sector = event.data?.heat?.sector;
              if (sector) onPick(sector);
            },
          }}
        />
      </div>

      <div className="panel overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-ink-800 text-left text-[11px] uppercase tracking-wide text-ink-600">
              <th className="px-3 py-2 font-medium">Sector</th>
              <th className="px-3 py-2 text-right font-medium">Picks</th>
              <th className="px-3 py-2 text-right font-medium">Companies</th>
              <th className="px-3 py-2 text-right font-medium">Share</th>
              <th className="px-3 py-2 text-right font-medium">Median upside</th>
            </tr>
          </thead>
          <tbody>
            {sectors.map((heat) => (
              <tr
                key={heat.sector}
                onClick={() => onPick(heat.sector)}
                className="cursor-pointer border-b border-ink-900 hover:bg-ink-850/60"
              >
                <td className="px-3 py-1.5 text-ink-200">{heat.sector}</td>
                <td className="tnum px-3 py-1.5 text-right text-ink-100">{heat.picks}</td>
                <td className="tnum px-3 py-1.5 text-right text-ink-400">{heat.total}</td>
                <td className="tnum px-3 py-1.5 text-right text-ink-300">
                  {plain(heat.share_pct, 0)}%
                </td>
                <td className="tnum px-3 py-1.5 text-right text-buy">
                  {percent(heat.median_upside_pct, 0)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
