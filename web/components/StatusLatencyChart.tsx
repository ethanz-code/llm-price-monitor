"use client";

import { useState } from "react";
import { Legend, Line, LineChart, CartesianGrid, ResponsiveContainer, Tooltip as ReTooltip, XAxis, YAxis } from "recharts";
import { ChartBubble, useChartTheme } from "./chartTheme";
import { formatTime } from "@/lib/format";
import type { LatencyPoint } from "@/lib/channelStatus";

interface TooltipEntry {
  name?: string | number;
  value?: number | string;
  color?: string;
  payload?: { label: string };
}

/** 悬停气泡：时间 + 当次各渠道延迟（带系列色点）。 */
function LatencyTooltip({ active, payload }: { active?: boolean; payload?: TooltipEntry[] }) {
  if (!active || !payload?.length) return null;
  return (
    <ChartBubble
      label={payload[0]?.payload?.label}
      rows={payload.map((entry) => ({
        color: entry.color,
        name: entry.name,
        value: typeof entry.value === "number" ? `${Math.round(entry.value)}ms` : "—",
      }))}
    />
  );
}

/** 渠道延迟趋势：每个渠道一条线（ms），延迟抬升往往先于故障出现。
 *  图例可点选隐藏/显示渠道，聚焦想看的线路；全隐藏时图表自然留空。
 *  调用方需保证可成图（至少一个渠道带延迟、检测点 ≥ 2），否则不要渲染本组件。 */
export function StatusLatencyChart({ points, channels }: { points: LatencyPoint[]; channels: string[] }) {
  const { axisColor, gridColor, palette } = useChartTheme();
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());

  function toggleChannel(name: string) {
    setHidden((previous) => {
      const next = new Set(previous);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  const data = points.map((point) => ({ label: formatTime(point.at), ...point.values }));

  return (
    <div style={{ height: 216 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
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
            width={52}
            tick={{ fill: axisColor, fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            domain={["auto", "auto"]}
            tickFormatter={(v: number) => `${v}ms`}
          />
          <ReTooltip content={<LatencyTooltip />} cursor={{ stroke: gridColor }} />
          <Legend
            iconSize={8}
            iconType="circle"
            wrapperStyle={{ cursor: "pointer" }}
            onClick={(entry) => {
              if (entry.value) toggleChannel(entry.value);
            }}
            formatter={(value) => (
              <span
                className="mono"
                style={{ color: "var(--text-2)", fontSize: 12, opacity: hidden.has(value) ? 0.4 : 1 }}
              >
                {value}
              </span>
            )}
          />
          {channels.map((name, index) => (
            <Line
              key={name}
              type="monotone"
              dataKey={name}
              name={name}
              stroke={palette[index % palette.length]}
              strokeWidth={1.5}
              dot={false}
              connectNulls
              hide={hidden.has(name)}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
