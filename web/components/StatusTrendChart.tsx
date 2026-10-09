"use client";

import { useMemo } from "react";
import { rateLevel, type AvailabilityPoint, type RateLevel } from "@/lib/channelStatus";
import { TimeSeriesChart, type TimeSeries } from "./TimeSeriesChart";

/** 三档图例文案：与分段着色、KPI 阈值一致。 */
const RATE_LEVEL_LABELS: Record<RateLevel, string> = {
  ok: "≥80% 优秀",
  warn: "60–80% 警告",
  down: "<60% 不及格",
};

/** 整站可用率趋势（原来的口径）：每个检测点一个样本，Y = 正常渠道占比（0–100%），
 *  线与填充按三档着色，值越低颜色越警示；图下有色档说明。 */
export function StatusTrendChart({
  times,
  values,
  window: windowProp,
  onWindowChange,
}: {
  times: number[];
  values: number[];
  window?: [number, number] | null;
  onWindowChange?: (value: [number, number] | null) => void;
}) {
  const series: TimeSeries[] = useMemo(() => [{ name: "渠道正常率", values }], [values]);
  const levelOf = useMemo(() => (index: number) => rateLevel(values[index] ?? 100), [values]);

  return (
    <TimeSeriesChart
      times={times}
      series={series}
      step
      yDomain={[0, 100]}
      yFormat={(v) => `${Math.round(v)}%`}
      height={220}
      levelOf={levelOf}
      levelLabels={RATE_LEVEL_LABELS}
      window={windowProp}
      onWindowChange={onWindowChange}
    />
  );
}
