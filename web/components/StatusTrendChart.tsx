"use client";

import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip as ReTooltip, XAxis, YAxis } from "recharts";
import { ChartBubble, useChartTheme } from "./chartTheme";
import { formatTime } from "@/lib/format";
import { rateLevel, downNamesLabel, type AvailabilityPoint, type RateLevel } from "@/lib/channelStatus";

/** 三档图例文案：与分段着色、KPI 阈值一致。 */
const RATE_LEVEL_LABELS: Record<RateLevel, string> = {
  ok: "≥95% 正常",
  warn: "80–95% 有渠道异常",
  down: "<80% 大面积异常",
};

interface ChartPoint {
  label: string;
  pct: number;
  level: RateLevel;
  down: string[];
}

interface TooltipPayloadItem {
  payload: ChartPoint;
}

/** 悬停气泡：时间 + 正常率；有渠道异常时单独点名异常渠道（红色标识）。 */
function RateTooltip({ active, payload }: { active?: boolean; payload?: TooltipPayloadItem[] }) {
  const { statusColors } = useChartTheme();
  if (!active || !payload?.length) return null;
  const point = payload[0]?.payload;
  const rows: { color?: string; name: React.ReactNode; value: React.ReactNode }[] = [
    { color: statusColors[point.level], name: "渠道正常率", value: `${point.pct}%` },
  ];
  if (point.down.length > 0) {
    rows.push({
      color: statusColors.down,
      name: "异常渠道",
      value: (
        <span style={{ overflowWrap: "anywhere", flex: 1, minWidth: 0 }}>{downNamesLabel(point.down)}</span>
      ),
    });
  }
  return <ChartBubble label={point.label} rows={rows} />;
}

/** 检测点按当次档位着色；只画属于本段的点，段间共用的边界邻点不重复画。 */
function LevelDot({ level, cx, cy, payload }: { level: RateLevel; cx?: number; cy?: number; payload?: ChartPoint }) {
  const { statusColors } = useChartTheme();
  if (level !== payload?.level || cx == null || cy == null) return null;
  return <circle cx={cx} cy={cy} r={2.5} fill={statusColors[level]} />;
}

/** 连续同档位的检测点切成一段：每段一条 Area，低值段整段换警示色。 */
function segmentRuns(data: ChartPoint[]): { level: RateLevel; key: string; start: number; end: number }[] {
  const runs: { level: RateLevel; key: string; start: number; end: number }[] = [];
  data.forEach((point, index) => {
    const last = runs[runs.length - 1];
    if (last?.level === point.level) {
      last.end = index;
      return;
    }
    runs.push({ level: point.level, key: `seg${runs.length}`, start: index, end: index });
  });
  return runs;
}

/** 渠道可用率阶梯图：每个检测点一个样本，Y = 正常渠道占比（0–100%）。
 *  线与填充按三档着色，值越低颜色越警示；图下有色档说明。 */
export function StatusTrendChart({ points }: { points: AvailabilityPoint[] }) {
  const { dark, axisColor, gridColor, statusColors } = useChartTheme();

  const data: ChartPoint[] = points.map((point) => ({
    label: formatTime(point.at),
    pct: point.pct,
    level: rateLevel(point.pct),
    down: point.down,
  }));

  if (data.length < 2) {
    return (
      <p style={{ color: "var(--text-3)", fontSize: 13, margin: 0 }}>
        至少两个检测点后这里会出现可用率趋势图；当前 {data.length} 个。
      </p>
    );
  }

  const runs = segmentRuns(data);
  // 每段在自己范围外多带一个值相同的邻点，相邻段的线才能首尾相接
  const series = data.map((point, index) => {
    const segments: Record<string, number | null> = {};
    for (const run of runs) {
      if (index >= run.start - 1 && index <= run.end + 1) segments[run.key] = point.pct;
    }
    return { ...point, ...segments };
  });

  return (
    <div>
      <div style={{ height: 220 }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={series} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
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
              {(Object.keys(statusColors) as RateLevel[]).map((level) => (
                <linearGradient key={level} id={`status-rate-${level}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={statusColors[level]} stopOpacity={dark ? 0.25 : 0.16} />
                  <stop offset="100%" stopColor={statusColors[level]} stopOpacity={0} />
                </linearGradient>
              ))}
            </defs>
            {runs.map((run) => (
              <Area
                key={run.key}
                type="stepAfter"
                dataKey={run.key}
                name="渠道正常率"
                stroke={statusColors[run.level]}
                strokeWidth={2}
                fill={`url(#status-rate-${run.level})`}
                connectNulls={false}
                dot={<LevelDot level={run.level} />}
                activeDot={{ r: 4, fill: statusColors[run.level] }}
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 14px", paddingTop: 8 }}>
        {(Object.keys(RATE_LEVEL_LABELS) as RateLevel[]).map((level) => (
          <span
            key={level}
            style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-3)" }}
          >
            <span aria-hidden style={{ width: 8, height: 8, borderRadius: 2, background: statusColors[level], flexShrink: 0 }} />
            {RATE_LEVEL_LABELS[level]}
          </span>
        ))}
      </div>
    </div>
  );
}
