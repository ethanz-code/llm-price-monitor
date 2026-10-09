"use client";

import { useMemo, useState } from "react";
import {
  Area,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as ReTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Sel, Seg } from "./ui";
import { ChartBubble, useChartTheme } from "./chartTheme";
import { currencySymbol, dayKey, formatPrice, toCnyPrice } from "@/lib/format";
import type { PriceRecord } from "@/lib/types";

interface SeriesPoint {
  date: string;
  day: string;
  input: number | null;
  output: number | null;
}

interface Series {
  key: string;
  label: string;
  unit: string;
  points: SeriesPoint[];
}

const RANGE_OPTIONS = [
  { value: "7", label: "近7天" },
  { value: "30", label: "近30天" },
  { value: "90", label: "近90天" },
  { value: "all", label: "全部" },
];

interface TooltipEntry {
  payload?: { day?: string; input?: number | null; output?: number | null };
}

/** 按天聚合的输入/输出价格趋势（同日多次取最低，本地时区分桶）；同一 series 内单位一致。
 *  传 rate 时 USD 价按汇率折算成 RMB 再画（单位漂移仍按原始单位判断），无汇率回落原币。 */
export function PriceTrendChart({ records, rate }: { records: PriceRecord[]; rate?: number | null }) {
  const { dark, lineColor, secondaryColor, axisColor, gridColor } = useChartTheme();

  function PriceTooltip({ active, payload }: { active?: boolean; payload?: TooltipEntry[] }) {
    if (!active || !payload?.length) return null;
    const point = payload[0]?.payload;
    if (!point) return null;
    const rows: { color: string; name: string; value: string }[] = [];
    if (point.input != null) rows.push({ color: lineColor, name: "输入价", value: `${symbol}${formatPrice(point.input)}` });
    if (point.output != null) rows.push({ color: secondaryColor, name: "输出价", value: `${symbol}${formatPrice(point.output)}` });
    return <ChartBubble label={point.day} rows={rows} />;
  }

  const seriesList = useMemo<Series[]>(() => {
    const buckets = new Map<string, { unit: string; byDay: Map<string, { i: number[]; o: number[] }> }>();
    for (const row of records) {
      if (row.input_price === null && row.output_price === null) continue;
      const key = `${row.site_id} · ${row.model}`;
      const day = dayKey(row.captured_at);
      const entry = buckets.get(key) ?? { unit: row.unit, byDay: new Map() };
      if (entry.unit !== row.unit) continue; // 单位漂移的混在一起会误导，跳过
      const dayEntry = entry.byDay.get(day) ?? { i: [], o: [] };
      if (row.input_price !== null) dayEntry.i.push(toCnyPrice(row.input_price, row.unit, rate) ?? row.input_price);
      if (row.output_price !== null) dayEntry.o.push(toCnyPrice(row.output_price, row.unit, rate) ?? row.output_price);
      entry.byDay.set(day, dayEntry);
      buckets.set(key, entry);
    }
    const list: Series[] = [];
    for (const [key, { unit, byDay }] of buckets) {
      const points = [...byDay.entries()]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([day, { i, o }]) => ({
          date: day,
          day: day.slice(5),
          input: i.length ? Math.min(...i) : null,
          output: o.length ? Math.min(...o) : null,
        }));
      if (points.length >= 2) list.push({ key, label: key, unit, points });
    }
    return list.sort((a, b) => b.points.length - a.points.length);
  }, [records]);

  const [active, setActive] = useState<string | null>(null);
  const [range, setRange] = useState("all");
  const series = seriesList.find((s) => s.key === active) ?? seriesList[0];
  const converted = rate != null && seriesList.some((s) => s.unit.toUpperCase().startsWith("USD"));
  const symbol = converted ? "¥" : currencySymbol(series?.unit ?? "");
  const unitLabel = converted ? "CNY/1M tokens（按汇率折算）" : series?.unit ?? "";
  const cutoff = range === "all" ? null : dayKey(Date.now() / 1000 - Number(range) * 86400);
  const visiblePoints = cutoff ? (series?.points.filter((point) => point.date >= cutoff) ?? []) : (series?.points ?? []);

  if (!series) {
    return (
      <p style={{ color: "var(--text-2)", margin: 0, padding: "8px 0" }}>
        这个模型的历史数据还不多，暂时画不出趋势；继续采集后这里会出现折线图。
      </p>
    );
  }

  // 数据点少时压低图表高度：稀疏折线撑满高容器会显得"图挂了"
  const sparse = visiblePoints.length < 5;
  const chartHeight = visiblePoints.length <= 4 ? 150 : 260;

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "4px 12px", marginBottom: 8 }}>
        <span style={{ fontWeight: 550, fontSize: 15 }}>价格趋势</span>
        <span style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
          {seriesList.length > 1 && (
            <Sel
              value={series.key}
              onChange={setActive}
              style={{ flex: 1, minWidth: 0, maxWidth: 320 }}
              options={seriesList.map((s) => ({ value: s.key, label: s.label }))}
            />
          )}
          <Seg value={range} onChange={setRange} options={RANGE_OPTIONS} />
        </span>
      </div>
      {visiblePoints.length === 0 && (
        <p className="empty" style={{ padding: "32px 0" }}>
          该时间范围内暂无数据点；切换到「全部」可查看完整历史。
        </p>
      )}
      <div style={{ height: chartHeight, overflow: "hidden", display: visiblePoints.length === 0 ? "none" : undefined }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={visiblePoints}
            margin={{ top: 8, right: 28, bottom: 4, left: 4 }}
          >
            <CartesianGrid stroke={gridColor} vertical={false} />
            <XAxis
              dataKey="day"
              tick={{ fill: axisColor, fontSize: 12 }}
              tickLine={false}
              axisLine={{ stroke: gridColor }}
              tickMargin={8}
              padding={{ left: 12, right: 12 }}
              interval="preserveStartEnd"
              minTickGap={24}
            />
            <YAxis
              width={56}
              tick={{ fill: axisColor, fontSize: 12 }}
              tickLine={false}
              axisLine={false}
              domain={["auto", "auto"]}
              tickFormatter={(v: number) => formatPrice(v)}
            />
            <ReTooltip content={<PriceTooltip />} cursor={{ stroke: gridColor }} />
            <defs>
              <linearGradient id="price-input-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={lineColor} stopOpacity={dark ? 0.2 : 0.14} />
                <stop offset="100%" stopColor={lineColor} stopOpacity={0} />
              </linearGradient>
            </defs>
            <Area
              type="monotone"
              dataKey="input"
              name="输入价"
              stroke={lineColor}
              strokeWidth={2}
              fill="url(#price-input-fill)"
              dot={{ r: 3, strokeWidth: 0, fill: lineColor }}
              activeDot={{ r: 4 }}
            />
            <Line
              type="monotone"
              dataKey="output"
              name="输出价"
              stroke={secondaryColor}
              strokeWidth={1.5}
              strokeDasharray="4 4"
              dot={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p style={{ color: "var(--text-3)", fontSize: 12, margin: "8px 0 0" }}>
        {sparse && <span style={{ color: "var(--tone-yellow-text)" }}>数据点还少，趋势仅供参考 · </span>}
        <span className="mono">{series.label}</span> · 单位 <span className="mono">{unitLabel}</span> ·
        同日多次采集取最低，虚线为输出价
      </p>
    </div>
  );
}
