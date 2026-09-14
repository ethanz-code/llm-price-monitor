"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  Area,
  AreaChart,
  ResponsiveContainer,
  Tooltip as ReTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Sel } from "./ui";
import { ChartBubble, useChartTheme } from "./chartTheme";
import { currencySymbol, formatPrice, formatTime, toCnyPrice } from "@/lib/format";
import type { TrendRecord } from "@/lib/types";

interface TrendPoint {
  ts: number;
  label: string;
  min: number;
}

interface TrendSeries {
  model: string;
  unit: string;
  points: TrendPoint[];
}

/** 按模型统一计算全站最低输入价：各站点、各分组、各单位的报价折算后，
 *  按小时合并取该小时观测到的最低价，一个模型一条线；
 *  不区分站点与分组——站间采集时间错开、档位价不同，逐点直连只会画出无意义的锯齿。
 *  activeModels 传入当前仍在采集的模型名：已从配置移除的模型不再出现在图里。 */
function buildSeries(records: TrendRecord[], rate: number | null | undefined, activeModels: Set<string>): TrendSeries[] {
  const byModel = new Map<string, TrendRecord[]>();
  for (const row of records) {
    if (row.input_price === null || !activeModels.has(row.model)) continue;
    const list = byModel.get(row.model) ?? [];
    list.push(row);
    byModel.set(row.model, list);
  }
  const seriesList: TrendSeries[] = [];
  for (const [model, rows] of byModel) {
    const byBucket = new Map<number, number>();
    for (const row of rows) {
      if (row.input_price === null) continue;
      const price = toCnyPrice(row.input_price, row.unit, rate) ?? row.input_price;
      const slot = Math.floor(row.captured_at / 3600) * 3600;
      const prev = byBucket.get(slot);
      if (prev === undefined || price < prev) byBucket.set(slot, price);
    }
    const points = [...byBucket.entries()]
      .sort(([a], [b]) => a - b)
      .map(([slot, min]) => ({ ts: slot, label: formatTime(slot), min }));
    if (points.length >= 2) seriesList.push({ model, unit: "", points });
  }
  return seriesList.sort((a, b) => b.points.length - a.points.length);
}

/** hero 侧栏实时最低价折线：默认展示数据点最多的模型，可切换；
 *  每小时取一次全站最低价，至少 2 个点才能成线，不足时不展示。 */
export function HeroTrendChart({
  records,
  rate,
  activeModels,
}: {
  records: TrendRecord[];
  rate?: number | null;
  activeModels?: string[];
}) {
  const { dark, lineColor } = useChartTheme();
  const modelSet = useMemo(() => new Set(activeModels ?? []), [activeModels]);
  // 未传 activeModels 时不做过滤（如独立预览），传了则只画当前配置里的模型
  const seriesList = useMemo(
    () => buildSeries(records, rate, activeModels ? modelSet : new Set(records.map((r) => r.model))),
    [records, rate, activeModels, modelSet],
  );
  const [active, setActive] = useState<string | null>(null);
  const series = seriesList.find((s) => s.model === active) ?? seriesList[0];

  // 数据不够画线（按天聚合后不足 2 点）时给空态提示，不再整块消失
  if (!series) {
    return (
      <div>
        <span className="hero-side-title">模型最低价</span>
        <p style={{ color: "var(--text-3)", fontSize: 13, lineHeight: 1.6, margin: "10px 0 0" }}>
          价格走势还在积累：每小时记一次全站最低价，攒够两个小时就能画出第一条线。
        </p>
      </div>
    );
  }

  // 全站混合报价：有汇率时统一折成 CNY；没有汇率则只能画同单位的站点， symbol 跟随多数单位
  const converted = rate != null;
  const symbol = converted ? "¥" : currencySymbol("USD");
  const unitLabel = converted ? "CNY/1M tokens（按汇率折算）" : "USD/1M tokens";
  const last = series.points[series.points.length - 1];

  function HeroTooltip({ active, payload }: { active?: boolean; payload?: { payload?: { label?: string; min?: number } }[] }) {
    if (!active || !payload?.length) return null;
    const point = payload[0]?.payload;
    if (!point) return null;
    return <ChartBubble label={point.label ?? ""} rows={[{ name: "全站最低输入价", value: `${symbol}${formatPrice(point.min)}` }]} />;
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
            ariaLabel="选择模型"
            style={{ flex: 1, minWidth: 0, maxWidth: 180 }}
          />
        )}
      </div>
      <div style={{ height: 120, marginTop: 10 }}>
        <ResponsiveContainer width="100%" height="100%">
          {/* recharts 3 起 LineChart 不再渲染混用的 Area，面积折线必须用 AreaChart */}
          <AreaChart data={series.points} margin={{ top: 8, right: 46, bottom: 0, left: 0 }}>
            <XAxis dataKey="label" hide />
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
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <p style={{ color: "var(--text-3)", fontSize: 12, margin: "6px 0 0" }}>
        <span className="mono">{series.model}</span> · 单位 <span className="mono">{unitLabel}</span> ·
        每小时取全站最低 · 最新 <span className="mono" style={{ color: "var(--text-2)" }}>{symbol}{formatPrice(last.min)}</span>
        <span style={{ color: "var(--text-3)" }}>（{last.label}）</span>
      </p>
      <Link href="/history" className="landing-more" style={{ display: "inline-block", marginTop: 8 }}>
        查看价格趋势 →
      </Link>
    </div>
  );
}
