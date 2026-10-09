"use client";

import { TimeSeriesChart, type TimeSeries } from "./TimeSeriesChart";

/** 分组延迟趋势：每个渠道一条线（ms），图例可点选隐藏；时间轴与可用率图联动。
 *  图例隐藏状态受控（与分享图共用同一份），由上层传入。 */
export function StatusLatencyChart({
  times,
  series,
  window: windowProp,
  onWindowChange,
  hiddenSeries,
  onToggleSeries,
}: {
  times: number[];
  series: TimeSeries[];
  window?: [number, number] | null;
  onWindowChange?: (value: [number, number] | null) => void;
  hiddenSeries?: ReadonlySet<string>;
  onToggleSeries?: (name: string) => void;
}) {
  return (
    <TimeSeriesChart
      times={times}
      series={series}
      yFormat={(v) => `${Math.round(v)}ms`}
      height={216}
      window={windowProp}
      onWindowChange={onWindowChange}
      hiddenSeries={hiddenSeries}
      onToggleSeries={onToggleSeries}
    />
  );
}
