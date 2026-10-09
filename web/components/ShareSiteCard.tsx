"use client";

import { useId } from "react";
import {
  buildUptimeBuckets,
  downNamesLabel,
  rateLevel,
  type AvailabilityPoint,
  type ChannelDotRow,
  type RateLevel,
  type UptimeBucket,
} from "@/lib/channelStatus";
import { formatTime } from "@/lib/format";
import { PALETTE_DARK, PALETTE_LIGHT } from "./chartTheme";
import { quantileRange } from "./TimeSeriesChart";
import { share } from "@/lib/copy";

export type ShareTheme = "light" | "dark";

/** 分享图双主题配色：色值一律取自站内主题变量（globals.css 的 :root / [data-theme="dark"]）
 *  ——底色以 --bg 为基准（亮色中间档用 --panel-2），面板取 --panel、边框取 --border、
 *  文字取 --text/--text-2/--text-3，状态三档与系列色板走 chartTheme 同一套。
 *  看到的页面什么样，导出的图就什么样。 */
const SHARE_PALETTES: Record<
  ShareTheme,
  {
    bgGradient: string;
    /** 面板底色（--panel）：站点面板靠"底色分层"不描边，但分享图裁掉了页底、无处分层，
     *  所以在这里显式填充。底色每一档都刻意压深于面板，保证面板落在渐变任何位置都分得出来。 */
    panel: string;
    panelBorder: string;
    divider: string;
    gridLine: string;
    axisColor: string;
    text: string;
    muted: string;
    faint: string;
    ok: string;
    warn: string;
    down: string;
    pill: string;
    /** 趋势填充顶部不透明度：与站内图表渐变填充同值（暗 0x1F / 亮 0x12） */
    fillAlpha: number;
    chartPalette: string[];
  }
> = {
  dark: {
    bgGradient: "linear-gradient(165deg, #0a0c0e 0%, #0e1216 58%, #0a0c0e 100%)",
    panel: "#121418",
    panelBorder: "1px solid rgba(255,255,255,0.08)",
    divider: "1px solid rgba(255,255,255,0.07)",
    gridLine: "rgba(255,255,255,0.06)",
    axisColor: "#6E7478",
    text: "#e6e8ec",
    muted: "#9aa0a8",
    faint: "#858c94",
    ok: "#C8FF00",
    warn: PALETTE_DARK[2],
    down: PALETTE_DARK[3],
    pill: "rgba(255,255,255,0.06)",
    fillAlpha: 0.12,
    chartPalette: PALETTE_DARK,
  },
  light: {
    bgGradient: "linear-gradient(165deg, #f7faff 0%, #eef3f9 58%, #f7faff 100%)",
    panel: "#ffffff",
    panelBorder: "1px solid rgba(20,45,80,0.08)",
    divider: "1px solid rgba(20,45,80,0.07)",
    gridLine: "rgba(0,0,0,0.06)",
    axisColor: "#9B9B98",
    text: "#212a33",
    muted: "#5d6a77",
    faint: "#6b7480",
    ok: "#86C200",
    warn: PALETTE_LIGHT[2],
    down: PALETTE_LIGHT[3],
    pill: "rgba(20,45,80,0.05)",
    fillAlpha: 0.07,
    chartPalette: PALETTE_LIGHT,
  },
};

const RATE_LEVEL_LABELS: Record<RateLevel, string> = {
  ok: "≥80% 优秀",
  warn: "60–80% 警告",
  down: "<60% 不及格",
};

/** 绘图区留白：与站内 TimeSeriesChart 的 PAD 一致，左轴标签 + 底部时间刻度。 */
const PAD = { left: 46, right: 12, top: 10, bottom: 22 };
const CHART_W = 864;
const CHART_H = 170;
const PLOT_W = CHART_W - PAD.left - PAD.right;
const PLOT_H = CHART_H - PAD.top - PAD.bottom;

/** 延迟图输入：与站内 TimeSeriesChart 同构——统一时间轴 + 每渠道一条值序列
 *  （缺数null），由 buildChannelModel 产出，保证与页面延迟图画的是同一条线。 */
export interface ShareLatencyModel {
  times: number[];
  series: { name: string; values: (number | null)[] }[];
}

const FONT = `-apple-system, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif`;
const MONO = `ui-monospace, "SF Mono", Menlo, Consolas, monospace`;

/** 同档位连续检测点切成一段，供逐段着色。 */
function levelRuns(levels: RateLevel[]): { level: RateLevel; start: number; end: number }[] {
  const runs: { level: RateLevel; start: number; end: number }[] = [];
  levels.forEach((level, index) => {
    const last = runs[runs.length - 1];
    if (last?.level === level) {
      last.end = index;
      return;
    }
    runs.push({ level, start: index, end: index });
  });
  return runs;
}

/** 可用率阶梯图路径：与站内主图同画法——水平保持到下一检测点再垂直跳变；
 *  每档一段，段尾多画一个邻点让相邻段首尾相接。坐标为绘图区相对坐标。 */
function availabilityPaths(points: AvailabilityPoint[], w: number, h: number) {
  const x = (i: number) => (i / (points.length - 1)) * w;
  const y = (pct: number) => (1 - pct / 100) * h;
  const levels = points.map((point) => rateLevel(point.pct));
  return levelRuns(levels).map((run) => {
    const from = run.start;
    const to = Math.min(run.end + 1, points.length - 1);
    let line = "";
    let prevY = 0;
    for (let i = from; i <= to; i += 1) {
      const py = y(points[i].pct);
      line += i === from ? `M ${x(i)} ${py}` : ` L ${x(i)} ${prevY} L ${x(i)} ${py}`;
      prevY = py;
    }
    return { level: run.level, line, area: `${line} L ${x(to)} ${h} L ${x(from)} ${h} Z` };
  });
}

/** 与站内主图同规则的时间刻度：最多 5 个，首个左对齐、末个右对齐、其余居中。 */
function timeTickMarks(count: number, atOf: (i: number) => number) {
  const ticks = Math.min(5, count);
  return Array.from({ length: ticks }, (_, k) => {
    const i = Math.round(((count - 1) * k) / (ticks - 1));
    return {
      x: PAD.left + (i / (count - 1)) * PLOT_W,
      label: formatTime(atOf(i)),
      anchor: (k === 0 ? "start" : k === ticks - 1 ? "end" : "middle") as "start" | "middle" | "end",
    };
  });
}

/** 网格与 Y 轴标签：5 行等分，标签右对齐在绘图区左侧，与站内主图同布局。 */
function ChartGrid({
  labels,
  gridLine,
  axisColor,
}: {
  labels: string[];
  gridLine: string;
  axisColor: string;
}) {
  return (
    <>
      {labels.map((label, r) => {
        const y = PAD.top + (r / (labels.length - 1)) * PLOT_H;
        return (
          <g key={r}>
            <line x1={PAD.left} x2={CHART_W - PAD.right} y1={y} y2={y} stroke={gridLine} />
            <text
              x={PAD.left - 6}
              y={y}
              fill={axisColor}
              fontSize={10.5}
              fontFamily={MONO}
              textAnchor="end"
              dominantBaseline="central"
            >
              {label}
            </text>
          </g>
        );
      })}
    </>
  );
}

function TimeAxis({
  marks,
  axisColor,
}: {
  marks: { x: number; label: string; anchor: "start" | "middle" | "end" }[];
  axisColor: string;
}) {
  return (
    <>
      {marks.map((mark, k) => (
        <text
          key={k}
          x={mark.x}
          y={CHART_H - PAD.bottom + 6}
          fill={axisColor}
          fontSize={10.5}
          fontFamily={MONO}
          textAnchor={mark.anchor}
          dominantBaseline="hanging"
        >
          {mark.label}
        </text>
      ))}
    </>
  );
}

/** 分享图 · 可用率趋势：阶梯面积图按三档着色，与站点状态页趋势图同口径
 *  （阶梯线 + 顶部渐变填充 + 5 档网格 + 时间刻度）。数据与页面同一个数组，不另抽稀。 */
function ShareTrendChart({ points, theme }: { points: AvailabilityPoint[]; theme: ShareTheme }) {
  const c = SHARE_PALETTES[theme];
  const rateColors: Record<RateLevel, string> = { ok: c.ok, warn: c.warn, down: c.down };
  // 低值段最后画压在正常段上面，与站内主图同序
  const runs = availabilityPaths(points, PLOT_W, PLOT_H).sort(
    (a, b) => (a.level === "ok" ? 0 : 1) - (b.level === "ok" ? 0 : 1),
  );
  const gradientId = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const avg = Math.round(points.reduce((sum, point) => sum + point.pct, 0) / points.length);
  return (
    <div
      style={{
        background: c.panel,
        border: c.panelBorder,
        borderRadius: 14,
        padding: "18px 20px 12px",
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span style={{ fontSize: 14.5, fontWeight: 600 }}>可用渠道占比趋势 · 最近 7 天</span>
        <span style={{ fontFamily: MONO, fontSize: 12.5, color: c.muted }}>7 日均值 {avg}%</span>
      </div>
      <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} width="100%" role="img" aria-label="可用渠道占比趋势图">
        <defs>
          {(Object.keys(rateColors) as RateLevel[]).map((level) => (
            <linearGradient key={level} id={`${gradientId}-${level}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor={rateColors[level]} stopOpacity={c.fillAlpha} />
              <stop offset="1" stopColor={rateColors[level]} stopOpacity={0} />
            </linearGradient>
          ))}
        </defs>
        <ChartGrid labels={[0, 25, 50, 75, 100].map((v) => `${v}%`)} gridLine={c.gridLine} axisColor={c.axisColor} />
        <TimeAxis marks={timeTickMarks(points.length, (i) => points[i].at)} axisColor={c.axisColor} />
        <g transform={`translate(${PAD.left}, ${PAD.top})`}>
          {runs.map((run, index) => (
            <g key={index}>
              <path d={run.area} fill={`url(#${gradientId}-${run.level})`} />
              <path d={run.line} fill="none" stroke={rateColors[run.level]} strokeWidth={1.25} strokeLinejoin="round" />
            </g>
          ))}
        </g>
      </svg>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "2px 14px" }}>
        {(Object.keys(RATE_LEVEL_LABELS) as RateLevel[]).map((level) => (
          <span key={level} style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11.5, color: c.muted }}>
            <span aria-hidden style={{ width: 8, height: 8, borderRadius: 2, background: rateColors[level], flexShrink: 0 }} />
            {RATE_LEVEL_LABELS[level]}
          </span>
        ))}
      </div>
    </div>
  );
}

/** 分享图 · 渠道延迟趋势：输入与站内延迟图同源同构（buildChannelModel 的产物），
 *  每渠道一条折线（ms），线性轴 + P2/P98 分位数量程，直线相连、缺数断笔、
 *  超出量程的尖峰裁到绘图区边缘——与站点状态页延迟图完全同口径。 */
function ShareLatencyChart({ times, series, theme }: { times: number[]; series: ShareLatencyModel["series"]; theme: ShareTheme }) {
  const c = SHARE_PALETTES[theme];
  // Hooks 必须在提前 return 之前调用完
  const clipId = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const values = series.flatMap((s) => s.values.filter((v): v is number => v != null));
  if (times.length < 2 || values.length === 0) return null;
  const [y0, y1] = quantileRange(values);
  const x = (i: number) => (i / (times.length - 1)) * PLOT_W;
  const y = (v: number) => (1 - (v - y0) / (y1 - y0)) * PLOT_H;
  // 每渠道一条：直线相连，缺数断笔重起，与站内 walkPath（step=false）同画法；颜色按系列下标取模
  const lines = series
    .map((s, index) => {
      let line = "";
      let pen = false;
      s.values.forEach((v, i) => {
        if (v == null) {
          pen = false;
          return;
        }
        line += pen ? ` L ${x(i)} ${y(v)}` : `M ${x(i)} ${y(v)}`;
        pen = true;
      });
      return line ? { name: s.name, color: c.chartPalette[index % c.chartPalette.length], line } : null;
    })
    .filter((line): line is { name: string; color: string; line: string } => line != null);
  const avg = Math.round(values.reduce((sum, v) => sum + v, 0) / values.length);
  return (
    <div
      style={{
        background: c.panel,
        border: c.panelBorder,
        borderRadius: 14,
        padding: "18px 20px 12px",
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span style={{ fontSize: 14.5, fontWeight: 600 }}>渠道延迟趋势 · 最近 7 天</span>
        <span style={{ fontFamily: MONO, fontSize: 12.5, color: c.muted }}>均值 {avg}ms</span>
      </div>
      <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} width="100%" role="img" aria-label="渠道延迟趋势图">
        <defs>
          <clipPath id={`${clipId}-plot`}>
            <rect x={0} y={0} width={PLOT_W} height={PLOT_H} />
          </clipPath>
        </defs>
        <ChartGrid labels={[0, 1, 2, 3, 4].map((r) => `${Math.round(y0 + ((y1 - y0) * r) / 4)}ms`)} gridLine={c.gridLine} axisColor={c.axisColor} />
        <TimeAxis marks={timeTickMarks(times.length, (i) => times[i])} axisColor={c.axisColor} />
        <g transform={`translate(${PAD.left}, ${PAD.top})`} clipPath={`url(#${clipId}-plot)`}>
          {lines.map((line) => (
            <path key={line.name} d={line.line} fill="none" stroke={line.color} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
          ))}
        </g>
      </svg>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "2px 14px" }}>
        {lines.map((line) => (
          <span key={line.name} style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11.5, color: c.muted, fontFamily: MONO }}>
            <span aria-hidden style={{ width: 8, height: 8, borderRadius: 999, background: line.color, flexShrink: 0 }} />
            {line.name}
          </span>
        ))}
      </div>
    </div>
  );
}

/** 渠道行内的迷你可用率色块条：与站点页「可用记录」列同口径（buildUptimeBuckets 分桶），
 *  静态色块不带悬停；空槽画弱化占位，保证各行条的横向位置对齐。 */
const STRIP_BUCKETS = 15;
function UptimeStrip({
  buckets,
  colors,
  emptyColor,
}: {
  buckets: (UptimeBucket | null)[];
  colors: Record<RateLevel, string>;
  emptyColor: string;
}) {
  return (
    <div style={{ display: "flex", gap: 2, flexShrink: 0 }} aria-hidden>
      {buckets.map((bucket, index) => (
        <span
          key={index}
          style={{
            width: 9,
            height: 12,
            borderRadius: 2,
            background: bucket ? colors[rateLevel(bucket.avg)] : emptyColor,
          }}
        />
      ))}
    </div>
  );
}

/** 中转站实时状况分享卡：纯 DOM、固定 1000px 宽、全部内联样式，
 *  由 ShareSiteButton 用 html-to-image 栅格化成 PNG；支持亮/暗两套配色。 */
export function ShareSiteCard({
  siteName,
  homepage,
  domain,
  availability,
  channels,
  latency,
  generatedAt,
  theme = "dark",
}: {
  siteName: string;
  homepage: string;
  /** 监控站自己的域名，用于分享回流 */
  domain: string;
  availability: AvailabilityPoint[];
  channels: ChannelDotRow[];
  /** 延迟趋势模型（buildChannelModel 产物），与站内延迟图同源 */
  latency: ShareLatencyModel;
  /** 生成时刻（秒级时间戳），由调用方传入避免 SSR/客户端差异 */
  generatedAt: number;
  /** 分享图配色主题，默认暗色（历史行为） */
  theme?: ShareTheme;
}) {
  const c = SHARE_PALETTES[theme];
  const rateColors: Record<RateLevel, string> = { ok: c.ok, warn: c.warn, down: c.down };
  const latest = availability[availability.length - 1];
  const pct = latest?.pct ?? null;
  const level = pct != null ? rateLevel(pct) : null;
  const downNames = latest?.down ?? [];
  const okChannels = channels.filter((channel) => channel.ok).length;
  const shown = channels.slice(0, 8);
  const hiddenCount = Math.max(channels.length - shown.length, 0);
  // 最近 15 次整站检测色点：与站点卡片上的色点条同口径
  const recent = availability.slice(-15);
  const brandName = "LLM 价格监控";
  const brandDesc = share.brandDesc;

  return (
    <div
      style={{
        width: 1000,
        boxSizing: "border-box",
        padding: "44px 48px 34px",
        background: c.bgGradient,
        color: c.text,
        fontFamily: FONT,
        display: "flex",
        flexDirection: "column",
        gap: 26,
      }}
    >
      {/* 品牌头：监控站是谁 + 域名 */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span aria-hidden style={{ width: 12, height: 12, borderRadius: 4, background: c.ok, flexShrink: 0 }} />
            <span style={{ fontSize: 21, fontWeight: 650, letterSpacing: 0.2 }}>{brandName}</span>
          </div>
          <span style={{ fontSize: 13.5, color: c.muted, maxWidth: 560, lineHeight: 1.6 }}>{brandDesc}</span>
        </div>
        <span style={{ fontFamily: MONO, fontSize: 14, color: c.muted, background: c.pill, padding: "7px 14px", borderRadius: 999 }}>
          {domain}
        </span>
      </div>

      <div style={{ height: 0, border: c.divider }} />

      {/* 站点与主页 */}
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 20 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
          <span style={{ fontSize: 34, fontWeight: 750, lineHeight: 1.15 }}>{siteName}</span>
          <span style={{ fontSize: 13.5, color: c.muted }}>中转站实时状况 · 最近 7 天渠道检测</span>
        </div>
        {homepage && (
          <span style={{ fontFamily: MONO, fontSize: 13.5, color: c.text, border: c.panelBorder, padding: "7px 14px", borderRadius: 8, flexShrink: 0 }}>
            {homepage}
          </span>
        )}
      </div>

      {/* KPI：当前可用率 / 渠道正常 / 检测次数 */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr 1fr",
          background: c.panel,
          border: c.panelBorder,
          borderRadius: 14,
          overflow: "hidden",
        }}
      >
        {[
          {
            label: "当前可用率",
            value: pct != null ? `${pct}%` : "—",
            color: level ? rateColors[level] : c.text,
          },
          {
            label: "渠道正常",
            value: channels.length > 0 ? `${okChannels}/${channels.length}` : "—",
            color: channels.length > 0 && okChannels === channels.length ? c.ok : c.warn,
          },
          { label: "检测次数 · 近 7 天", value: `${availability.length}`, color: c.text },
        ].map((kpi, index) => (
          <div
            key={kpi.label}
            style={{
              padding: "20px 24px 18px",
              display: "flex",
              flexDirection: "column",
              gap: 8,
              borderLeft: index > 0 ? c.divider : "none",
            }}
          >
            <span style={{ fontSize: 12.5, color: c.muted }}>{kpi.label}</span>
            <span style={{ fontFamily: MONO, fontSize: 32, fontWeight: 700, lineHeight: 1.1, color: kpi.color }}>{kpi.value}</span>
          </div>
        ))}
      </div>

      {/* 异常渠道点名 + 最近检测色点 */}
      {downNames.length > 0 && (
        <span style={{ fontSize: 14, color: c.down }}>
          异常渠道：{downNamesLabel(downNames)}
        </span>
      )}
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        <span style={{ fontSize: 12.5, color: c.muted, flexShrink: 0 }}>最近检测</span>
        <div style={{ display: "flex", gap: 5 }}>
          {recent.map((point) => (
            <span
              key={point.at}
              style={{ width: 20, height: 20, borderRadius: 5, background: rateColors[rateLevel(point.pct)], opacity: 0.9 }}
            />
          ))}
        </div>
      </div>

      {/* 趋势图：可用率必有（≥2 个检测点），延迟图仅有渠道带延迟时出现（与页面 showLatency 同判据） */}
      {availability.length >= 2 && <ShareTrendChart points={availability} theme={theme} />}
      {latency.times.length >= 2 && latency.series.length > 0 && (
        <ShareLatencyChart times={latency.times} series={latency.series} theme={theme} />
      )}

      {/* 渠道状态列表 */}
      {shown.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column" }}>
          {shown.map((channel, index) => {
            const last = channel.dots[channel.dots.length - 1];
            const latency = last?.latency != null ? `${last.latency}ms` : null;
            const availability7d = channel.availability7d != null ? `${channel.availability7d.toFixed(2)}%` : null;
            const metrics = [latency, availability7d].filter(Boolean).join(" · ");
            // 该渠道自己的可用率时段桶：与页面渠道行「可用记录」同口径，每次检测正常记 100%、异常记 0%
            const buckets = buildUptimeBuckets(
              channel.dots
                .filter((dot) => dot.at != null)
                .map((dot) => ({
                  at: dot.at as number,
                  pct: dot.ok ? 100 : 0,
                  down: dot.ok ? [] : [channel.name],
                })),
              STRIP_BUCKETS,
            );
            return (
              <div
                key={channel.name}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 16,
                  padding: "11px 4px",
                  borderTop: index === 0 ? c.divider : "none",
                  borderBottom: index < shown.length - 1 || hiddenCount > 0 ? c.divider : "none",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0, flex: 1 }}>
                  <span
                    aria-hidden
                    style={{ width: 9, height: 9, borderRadius: 3, flexShrink: 0, background: channel.ok ? c.ok : c.faint }}
                  />
                  <span style={{ fontSize: 15, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{channel.name}</span>
                  {(channel.provider || channel.model) && (
                    <span style={{ fontSize: 11.5, color: c.faint, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {[channel.provider, channel.model].filter(Boolean).join(" · ")}
                    </span>
                  )}
                </div>
                <UptimeStrip buckets={buckets} colors={rateColors} emptyColor={c.pill} />
                <span style={{ fontFamily: MONO, fontSize: 13.5, color: metrics ? c.muted : c.faint, flexShrink: 0 }}>
                  {metrics || "—"}
                </span>
              </div>
            );
          })}
          {hiddenCount > 0 && (
            <span style={{ fontSize: 12.5, color: c.faint, padding: "9px 4px 2px" }}>
              还有 {hiddenCount} 个渠道，去 {domain} 看完整状态
            </span>
          )}
        </div>
      )}

      {/* 页脚 */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 2 }}>
        <span style={{ fontFamily: MONO, fontSize: 13, color: c.muted }}>
          {brandName} · {domain}
        </span>
        <span style={{ fontFamily: MONO, fontSize: 13, color: c.faint }}>数据截至 {formatTime(generatedAt)}</span>
      </div>
    </div>
  );
}
