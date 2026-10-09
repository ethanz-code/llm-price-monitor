"use client";

/** 概览首页的精简访问面板：今日/近 30 天访问 KPI + 30 天 PV/UV 趋势小图；
 *  设备、热门页面与访问明细在完整统计页（/admin/analytics）。 */

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip as ReTooltip,
  XAxis,
} from "recharts";
import { apiSend } from "@/lib/api";
import type { AnalyticsSummary } from "@/lib/types";
import { ChartBubble, useChartTheme } from "./chartTheme";

interface TrendTooltipEntry {
  payload?: { day?: string; pv?: number; uv?: number };
}

function TrendTooltip({ active, payload }: { active?: boolean; payload?: TrendTooltipEntry[] }) {
  if (!active || !payload?.length) return null;
  const point = payload[0]?.payload;
  if (!point) return null;
  return (
    <ChartBubble
      label={point.day}
      rows={[
        { name: "访问量", value: point.pv ?? 0 },
        { name: "访客数", value: point.uv ?? 0 },
      ]}
    />
  );
}

export function TrafficPanel() {
  const { lineColor, secondaryColor, axisColor, gridColor } = useChartTheme();
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);

  useEffect(() => {
    let alive = true;
    // 首页的次要信息，读不到就不显示这块面板
    apiSend<AnalyticsSummary>("/api/analytics/summary", "GET")
      .then((data) => {
        if (alive) setSummary(data);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  if (!summary) return null;

  const kpis = [
    { value: summary.today_pv, label: "今日访问" },
    { value: summary.today_uv, label: "今日访客" },
    { value: summary.total_pv, label: "近 30 天访问" },
  ];

  return (
    <div className="panel">
      <div className="dash-panel-head">
        <span className="dash-panel-title">访问统计</span>
        <Link href="/admin/analytics" className="landing-more">
          完整统计 →
        </Link>
      </div>
      {summary.total_pv > 0 ? (
        <>
          <div style={{ display: "flex", gap: 28, flexWrap: "wrap", padding: "14px 20px 0" }}>
            {kpis.map((kpi) => (
              <div key={kpi.label}>
                <div className="mono" style={{ fontSize: 19, fontWeight: 600 }}>
                  {kpi.value}
                </div>
                <div style={{ fontSize: 12, color: "var(--text-3)", marginTop: 2 }}>{kpi.label}</div>
              </div>
            ))}
          </div>
          <div style={{ height: 120, padding: "4px 12px 6px 0", marginTop: 6 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={summary.daily} margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
                <CartesianGrid stroke={gridColor} vertical={false} />
                <XAxis
                  dataKey="day"
                  tick={{ fill: axisColor, fontSize: 11 }}
                  tickLine={false}
                  axisLine={{ stroke: gridColor }}
                  tickMargin={6}
                  minTickGap={48}
                  interval="preserveStartEnd"
                />
                <ReTooltip content={<TrendTooltip />} cursor={{ fill: "rgba(127,127,127,0.12)" }} />
                <Bar dataKey="pv" name="访问量" fill={lineColor} radius={[2, 2, 0, 0]} maxBarSize={10} />
                <Bar dataKey="uv" name="访客数" fill={secondaryColor} radius={[2, 2, 0, 0]} maxBarSize={10} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </>
      ) : (
        <p className="empty" style={{ padding: "24px 20px" }}>
          还没有访问记录，有人打开页面后就会开始统计。
        </p>
      )}
    </div>
  );
}
