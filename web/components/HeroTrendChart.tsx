"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  Area,
  LineChart,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip as ReTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Sel } from "./ui";
import { ChartBubble, useChartTheme } from "./chartTheme";
import { currencySymbol, dayKey, formatPrice, toCnyPrice } from "@/lib/format";
import type { PriceRecord } from "@/lib/types";

interface TrendPoint {
  day: string;
  min: number;
}

interface TrendSeries {
  model: string;
  unit: string;
  points: TrendPoint[];
}

/** 按模型聚合全站最低输入价：只统计该模型出现最多的单位（避免单位漂移误导），
 *  同日多次采集取最低；按天不足 2 点的模型画不出折线，直接不出现。 */
function buildSeries(records: PriceRecord[], rate?: number | null): TrendSeries[] {
  const byModel = new Map<string, PriceRecord[]>();
  for (const row of records) {
    if (row.input_price === null) continue;
    const list = byModel.get(row.model) ?? [];
    list.push(row);
    byModel.set(row.model, list);
  }
  const seriesList: TrendSeries[] = [];
  for (const [model, rows] of byModel) {
    const unitCount = new Map<string, number>();
    for (const row of rows) unitCount.set(row.unit, (unitCount.get(row.unit) ?? 0) + 1);
    const unit = [...unitCount.entries()].sort((a, b) => b[1] - a[1])[0][0];
    const byDay = new Map<string, number>();
    for (const row of rows) {
      if (row.unit !== unit || row.input_price === null) continue;
      const price = toCnyPrice(row.input_price, row.unit, rate) ?? row.input_price;
      const day = dayKey(row.captured_at);
      const prev = byDay.get(day);
      if (prev === undefined || price < prev) byDay.set(day, price);
    }
    const points = [...byDay.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([day, min]) => ({ day: day.slice(5), min }));
    if (points.length >= 2) seriesList.push({ model, unit, points });
  }
  return seriesList.sort((a, b) => b.points.length - a.points.length);
}

/** hero 侧栏实时最低价折线：默认展示数据点最多的模型，可切换；
 *  趋势至少要积累一周（≥7 个日度点）才有意义，不足时不展示。 */
export function HeroTrendChart({ records, rate }: { records: PriceRecord[]; rate?: number | null }) {
  const { dark, lineColor } = useChartTheme();
  const seriesList = useMemo(() => buildSeries(records, rate).filter((s) => s.points.length >= 7), [records, rate]);
  const [active, setActive] = useState<string | null>(null);
  const series = seriesList.find((s) => s.model === active) ?? seriesList[0];

  if (!series) return null;

  const converted = rate != null && series.unit.toUpperCase().startsWith("USD");
  const symbol = converted ? "¥" : currencySymbol(series.unit);
  const unitLabel = converted ? "CNY/1M tokens（按汇率折算）" : series.unit;
  const last = series.points[series.points.length - 1];

  function HeroTooltip({ active, payload }: { active?: boolean; payload?: { payload?: { day?: string; min?: number } }[] }) {
    if (!active || !payload?.length) return null;
    const point = payload[0]?.payload;
    if (!point) return null;
    return <ChartBubble label={point.day} rows={[{ name: "全站最低输入价", value: `${symbol}${formatPrice(point.min)}` }]} />;
  }

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <span className="hero-side-title">模型最低价</span>
        {seriesList.length > 1 && (
          <Sel
            value={series.model}
            onChange={setActive}
            options={seriesList.map((s) => ({ value: s.model, label: s.model }))}
            style={{ flex: 1, minWidth: 0, maxWidth: 180 }}
          />
        )}
      </div>
      <div style={{ height: 120, marginTop: 10 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={series.points} margin={{ top: 8, right: 46, bottom: 0, left: 0 }}>
            <XAxis dataKey="day" hide />
            <YAxis hide domain={["auto", "auto"]} />
            <ReTooltip content={<HeroTooltip />} />
            <defs>
              <linearGradient id="hero-trend-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={lineColor} stopOpacity={dark ? 0.28 : 0.2} />
                <stop offset="100%" stopColor={lineColor} stopOpacity={0} />
              </linearGradient>
            </defs>
            <Area
              type="monotone"
              dataKey="min"
              stroke={lineColor}
              strokeWidth={2}
              fill="url(#hero-trend-fill)"
              dot={false}
            />
            <ReferenceDot
              x={last.day}
              y={last.min}
              r={3.5}
              fill={lineColor}
              stroke="none"
              label={{
                value: formatPrice(last.min),
                position: "right",
                fill: "var(--text-2)",
                fontSize: 12,
                fontFamily: "var(--mono)",
              }}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p style={{ color: "var(--text-3)", fontSize: 12, margin: "6px 0 0" }}>
        <span className="mono">{series.model}</span> · 单位 <span className="mono">{unitLabel}</span> ·
        每日取最低价
      </p>
      <Link href="/history" className="landing-more" style={{ display: "inline-block", marginTop: 8 }}>
        查看价格趋势 →
      </Link>
    </div>
  );
}
