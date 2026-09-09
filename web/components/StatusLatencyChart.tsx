"use client";

import { TimeSeriesChart, type TimeSeries } from "./TimeSeriesChart";

/** 分组延迟趋势：每个渠道一条线（ms），图例可点选隐藏；时间轴与可用率图联动。 */
export function StatusLatencyChart({
  times,
  series,
  window: windowProp,
  onWindowChange,
}: {
  times: number[];
  series: TimeSeries[];
  window?: [number, number] | null;
  onWindowChange?: (value: [number, number] | null) => void;
}) {
  return (
    <TimeSeriesChart
      times={times}
      series={series}
      yFormat={(v) => `${Math.round(v)}ms`}
      height={216}
      window={windowProp}
      onWindowChange={onWindowChange}
    />
  );
}
