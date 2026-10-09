"use client";

import { StatusTrendChart } from "./StatusTrendChart";
import { StatusLatencyChart } from "./StatusLatencyChart";
import type { AvailabilityPoint, LatencyPoint } from "@/lib/channelStatus";

/** 渠道检测趋势：可用率与延迟两张图。各站点状态数据的时间跨度/粒度不统一，
 *  不做固定时间档位切换，直接展示存档里的全部检测点（时序接口有 limit 上限）。
 *  showLatency 为 false（无渠道自报延迟或检测点不足以成图）时，整个延迟区块不渲染。 */
export function StatusCharts({
  availability,
  latency,
  latencyNames,
  showLatency,
}: {
  availability: AvailabilityPoint[];
  latency: LatencyPoint[];
  latencyNames: string[];
  showLatency: boolean;
}) {
  return (
    <div style={{ display: "grid", gap: 6 }}>
      <h3 className="section-title">可用率趋势</h3>
      <StatusTrendChart points={availability} />
      {showLatency && (
        <>
          <div style={{ paddingTop: 24 }}>
            <h3 className="section-title">延迟趋势</h3>
          </div>
          <StatusLatencyChart points={latency} channels={latencyNames} />
        </>
      )}
    </div>
  );
}
