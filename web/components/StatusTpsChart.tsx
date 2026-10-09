"use client";

import { TimeSeriesChart, type TimeSeries } from "./TimeSeriesChart";

/** 分组出字速度趋势：每个渠道一条线（TPS，每秒出多少 token），图例可点选隐藏；时间轴与可用率图联动。 */
export function StatusTpsChart({
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
      yFormat={(v) => `${Math.round(v)} tps`}
      height={216}
      window={windowProp}
      onWindowChange={onWindowChange}
    />
  );
}
