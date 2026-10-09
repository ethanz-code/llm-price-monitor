"use client";

import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as ReTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Sel } from "./ui";
import { formatPrice } from "@/lib/format";
import { useTheme } from "@/app/providers";
import type { PriceRecord } from "@/lib/types";

interface SeriesPoint {
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

/** 按天聚合的输入/输出价格趋势；同一 series 内单位一致。 */
export function PriceTrendChart({ records }: { records: PriceRecord[] }) {
  const { dark } = useTheme();
  const axisColor = dark ? "#6E7478" : "#9B9B98";
  const gridColor = dark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.06)";
  const lineColor = dark ? "#C8FF00" : "#3F5600";
  const outputColor = dark ? "#9DA3A6" : "#787774";

  const seriesList = useMemo<Series[]>(() => {
    const buckets = new Map<string, { unit: string; byDay: Map<string, { i: number[]; o: number[] }> }>();
    for (const row of records) {
      if (row.input_price === null && row.output_price === null) continue;
      const key = `${row.site_id} · ${row.model}`;
      const day = new Date(row.captured_at * 1000).toISOString().slice(0, 10);
      const entry = buckets.get(key) ?? { unit: row.unit, byDay: new Map() };
      if (entry.unit !== row.unit) continue; // 单位漂移的混在一起会误导，跳过
      const dayEntry = entry.byDay.get(day) ?? { i: [], o: [] };
      if (row.input_price !== null) dayEntry.i.push(row.input_price);
      if (row.output_price !== null) dayEntry.o.push(row.output_price);
      entry.byDay.set(day, dayEntry);
      buckets.set(key, entry);
    }
    const list: Series[] = [];
    for (const [key, { unit, byDay }] of buckets) {
      const points = [...byDay.entries()]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([day, { i, o }]) => ({
          day: day.slice(5),
          input: i.length ? i.reduce((a, b) => a + b, 0) / i.length : null,
          output: o.length ? o.reduce((a, b) => a + b, 0) / o.length : null,
        }));
      if (points.length >= 2) list.push({ key, label: key, unit, points });
    }
    return list.sort((a, b) => b.points.length - a.points.length);
  }, [records]);

  const [active, setActive] = useState<string | null>(null);
  const series = seriesList.find((s) => s.key === active) ?? seriesList[0];

  if (!series) {
    return (
      <p style={{ color: "var(--text-2)", margin: 0, padding: "8px 0" }}>
        单个模型的历史数据点还不足两次，暂无趋势可画；继续积累采集记录后这里会出现折线图。
      </p>
    );
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <span style={{ fontWeight: 550, fontSize: 15 }}>价格趋势</span>
        {seriesList.length > 1 && (
          <Sel
            value={series.key}
            onChange={setActive}
            style={{ minWidth: 240 }}
            options={seriesList.map((s) => ({ value: s.key, label: s.label }))}
          />
        )}
      </div>
      <div style={{ height: 260, overflow: "hidden" }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={series.points}
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
            <ReTooltip
              contentStyle={{
                background: dark ? "#1B1F24" : "#FFFFFF",
                border: "1px solid var(--border-strong)",
                borderRadius: 8,
                fontSize: 13,
              }}
              labelStyle={{ color: axisColor, fontFamily: "var(--mono)" }}
              formatter={(value) => [formatPrice(value as number), ""]}
            />
            <Line
              type="monotone"
              dataKey="input"
              name="输入价"
              stroke={lineColor}
              strokeWidth={2}
              dot={{ r: 3, strokeWidth: 0, fill: lineColor }}
              activeDot={{ r: 4 }}
            />
            <Line
              type="monotone"
              dataKey="output"
              name="输出价"
              stroke={outputColor}
              strokeWidth={1.5}
              strokeDasharray="4 4"
              dot={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p style={{ color: "var(--text-3)", fontSize: 12, margin: "8px 0 0" }}>
        <span className="mono">{series.label}</span> · 单位 <span className="mono">{series.unit}</span> ·
        同日多次采集取均值，虚线为输出价
      </p>
    </div>
  );
}
