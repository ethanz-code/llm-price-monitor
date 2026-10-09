"use client";

import { useMemo, useState } from "react";
import {
  buildChannelModel,
  downsampleWorst,
  NARROW_CHART_POINTS,
  type AvailabilityPoint,
  type ChannelDotRow,
} from "@/lib/channelStatus";
import { useNarrow } from "@/lib/useNarrow";
import { StatusTrendChart } from "./StatusTrendChart";
import { StatusLatencyChart } from "./StatusLatencyChart";
import { StatusTpsChart } from "./StatusTpsChart";

/** 渠道检测趋势：可用率（整站聚合、三档着色）与延迟（按渠道）两张图。
 *  两张图共用同一个时间轴窗口（比例制，拖任意一张另一张跟着联动）。 */
export function StatusCharts({
  availability,
  channels,
}: {
  availability: AvailabilityPoint[];
  channels: ChannelDotRow[];
}) {
  const narrow = useNarrow();
  const [window, setWindow] = useState<[number, number] | null>(null);

  const chartAvailability = useMemo(
    () => (narrow ? downsampleWorst(availability, NARROW_CHART_POINTS, (a, b) => (a.pct < b.pct ? a : b)) : availability),
    [narrow, availability],
  );
  const availabilityModel = useMemo(
    () => ({ times: chartAvailability.map((point) => point.at), values: chartAvailability.map((point) => point.pct) }),
    [chartAvailability],
  );
  const latencyModel = useMemo(
    () =>
      buildChannelModel(
        channels,
        // 0 / 负值是站点自报的无效延迟，当缺数处理
        (dot) => (dot.latency != null && dot.latency > 0 ? dot.latency : null),
        narrow,
        (a, b) => (a.value >= b.value ? a : b),
      ),
    [channels, narrow],
  );
  const showLatency = latencyModel.times.length >= 2;
  const tpsModel = useMemo(
    () =>
      buildChannelModel(
        channels,
        // 0 / 负值当缺数处理
        (dot) => (dot.tps != null && dot.tps > 0 ? dot.tps : null),
        narrow,
        (a, b) => (a.value >= b.value ? a : b),
      ),
    [channels, narrow],
  );
  const showTps = tpsModel.times.length >= 2;

  return (
    <div style={{ display: "grid", gap: 14 }}>
      <h3 className="section-title">可用渠道占比趋势</h3>
      <StatusTrendChart
        times={availabilityModel.times}
        values={availabilityModel.values}
        window={window}
        onWindowChange={setWindow}
      />
      {showLatency && (
        <>
          <h3 className="section-title">延迟趋势</h3>
          <StatusLatencyChart
            times={latencyModel.times}
            series={latencyModel.series}
            window={window}
            onWindowChange={setWindow}
          />
        </>
      )}
      {showTps && (
        <>
          <h3 className="section-title">出字速度趋势（TPS）</h3>
          <StatusTpsChart
            times={tpsModel.times}
            series={tpsModel.series}
            window={window}
            onWindowChange={setWindow}
          />
        </>
      )}
    </div>
  );
}
