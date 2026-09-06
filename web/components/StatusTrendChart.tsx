"use client";

import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip as ReTooltip, XAxis, YAxis } from "recharts";
import { ChartBubble, useChartTheme } from "./chartTheme";
import { formatTime } from "@/lib/format";
import type { AvailabilityPoint } from "@/lib/channelStatus";

interface ChartPoint {
  label: string;
  pct: number;
  tip: string;
}

interface TooltipPayloadItem {
  payload: ChartPoint;
}

/** 悬停气泡：时间 + 当次正常率，异常时点名异常渠道。 */
function RateTooltip({ active, payload }: { active?: boolean; payload?: TooltipPayloadItem[] }) {
  const lineColor = useChartTheme().lineColor;
  if (!active || !payload?.length) return null;
  const point = payload[0]?.payload;
  return (
    <ChartBubble
      label={point.label}
      rows={[{ color: lineColor, name: "渠道正常率", value: point.tip }]}
    />
  );
}

/** 渠道可用率阶梯图：每个检测点一个样本，Y = 正常渠道占比（0–100%）。 */
export function StatusTrendChart({ points }: { points: AvailabilityPoint[] }) {
  const { dark, lineColor, axisColor, gridColor } = useChartTheme();

  const data: ChartPoint[] = points.map((point) => ({
    label: formatTime(point.at),
    pct: point.pct,
    tip: `${point.pct}% 正常${
      point.down.length ? `（${point.down.slice(0, 3).join("、")}${point.down.length > 3 ? " 等" : ""}）` : ""
    }`,
  }));

  if (data.length < 2) {
    return (
      <p style={{ color: "var(--text-3)", fontSize: 13, margin: 0 }}>
        至少两个检测点后这里会出现可用率趋势图；当前 {data.length} 个。
      </p>
    );
  }

  return (
    <div style={{ height: 220 }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
          <CartesianGrid stroke={gridColor} vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fill: axisColor, fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: gridColor }}
            tickMargin={8}
            minTickGap={40}
            interval="preserveStartEnd"
          />
          <YAxis
            domain={[0, 100]}
            width={42}
            tick={{ fill: axisColor, fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v: number) => `${v}%`}
          />
          <ReTooltip content={<RateTooltip />} cursor={{ stroke: gridColor }} />
          <defs>
            <linearGradient id="status-rate-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={lineColor} stopOpacity={dark ? 0.25 : 0.16} />
              <stop offset="100%" stopColor={lineColor} stopOpacity={0} />
            </linearGradient>
          </defs>
          <Area
            type="stepAfter"
            dataKey="pct"
            name="渠道正常率"
            stroke={lineColor}
            strokeWidth={2}
            fill="url(#status-rate-fill)"
            dot={{ r: 2.5, strokeWidth: 0, fill: lineColor }}
            activeDot={{ r: 4 }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
