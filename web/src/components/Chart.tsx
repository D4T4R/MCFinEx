/**
 * ECharts with only the pieces this app draws.
 *
 * Importing `echarts` wholesale pulls every chart type, every coordinate system
 * and the SVG renderer: 1,059 KB minified, 352 KB over the wire, and it was
 * loading on the table tab where nothing is plotted. Registering scatter,
 * treemap and bar against the canvas renderer is the same three charts for a
 * fraction of it.
 *
 * Anything added here has to be registered. An unregistered series type fails at
 * runtime with an empty chart and a console warning rather than at build, so a
 * new chart type silently renders nothing until somebody notices.
 */

import { BarChart, ScatterChart, TreemapChart } from 'echarts/charts';
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components';
import * as echarts from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import ReactEChartsCore from 'echarts-for-react/lib/core';

echarts.use([
  ScatterChart, TreemapChart, BarChart,
  GridComponent, TooltipComponent, LegendComponent,
  DataZoomComponent, VisualMapComponent,
  CanvasRenderer,
]);

interface Props {
  option: Record<string, unknown>;
  style?: React.CSSProperties;
  onEvents?: Record<string, (params: never) => void>;
}

export function Chart({ option, style, onEvents }: Props) {
  return (
    <ReactEChartsCore
      echarts={echarts}
      option={option}
      style={style}
      notMerge
      // Without this a series removed by a filter keeps its old points on screen:
      // ECharts merges by default, so "fewer companies" renders as "the same
      // companies" until something else forces a redraw.
      lazyUpdate={false}
      onEvents={onEvents}
    />
  );
}
