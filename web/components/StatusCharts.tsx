"use client";

import { useMemo, useState } from "react";
import { downsampleWorst, type AvailabilityPoint, type ChannelDotRow } from "@/lib/channelStatus";
import { useNarrow } from "@/lib/useNarrow";
import { StatusTrendChart } from "./StatusTrendChart";
import { StatusLatencyChart } from "./StatusLatencyChart";

/** 窄屏抽帧上限：小屏像素少，全量点既看不清也拖不动；抽到这个量依然顺滑。 */
const NARROW_CHART_POINTS = 240;

interface Sample {
  at: number;
  value: number;
}

/** 把各渠道的检测点对齐到统一时间轴：某时刻的值取「该渠道最近一次检测」的结果（阶梯保持），
 *  首次检测之前为 null。桌面端保留全部检测点；窄屏按桶保留"最坏"点，故障形状不丢。 */
function buildChannelModel(
  channels: ChannelDotRow[],
  pick: (dot: { ok: boolean; latency?: number }) => number | null,
  narrow: boolean,
  worse: (a: Sample, b: Sample) => Sample,
) {
  const per = channels
    .map((channel) => ({
      name: channel.name,
      dots: channel.dots
        .filter((dot) => dot.at != null)
        .map((dot) => ({ at: dot.at as number, value: pick(dot) }))
        .filter((dot): dot is Sample => dot.value != null),
    }))
    .filter((channel) => channel.dots.length > 0);
  const kept = narrow ? per.map((c) => ({ ...c, dots: downsampleWorst(c.dots, NARROW_CHART_POINTS, worse) })) : per;

  const seen = new Set<number>();
  kept.forEach((c) => c.dots.forEach((d) => seen.add(d.at)));
  const times = [...seen].sort((a, b) => a - b);

  const series = kept.map((c) => {
    const values: (number | null)[] = [];
    let cursor = 0;
    let last: number | null = null;
    for (const t of times) {
      while (cursor < c.dots.length && c.dots[cursor].at <= t) {
        last = c.dots[cursor].value;
        cursor += 1;
      }
      values.push(last);
    }
    return { name: c.name, values };
  });
  return { times, series };
}

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
    </div>
  );
}
