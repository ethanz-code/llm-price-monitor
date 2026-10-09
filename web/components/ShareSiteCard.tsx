"use client";

import {
  rateLevel,
  type AvailabilityPoint,
  type ChannelDotRow,
  type LatencyPoint,
  type RateLevel,
} from "@/lib/channelStatus";
import { formatTime } from "@/lib/format";
import { share } from "@/lib/copy";

export type ShareTheme = "light" | "dark";

/** 分享图双主题配色：暗色是历史默认；亮色对齐站内亮色主题（globals.css :root），状态色用亮色图表三档。 */
const SHARE_PALETTES: Record<
  ShareTheme,
  {
    bgGradient: string;
    panelBorder: string;
    divider: string;
    gridLine: string;
    text: string;
    muted: string;
    faint: string;
    ok: string;
    warn: string;
    down: string;
    pill: string;
    chartPalette: string[];
  }
> = {
  dark: {
    bgGradient: "linear-gradient(165deg, #0c1016 0%, #121a24 60%, #0d131b 100%)",
    panelBorder: "1px solid rgba(255,255,255,0.08)",
    divider: "1px solid rgba(255,255,255,0.07)",
    gridLine: "rgba(255,255,255,0.06)",
    text: "#e9eef5",
    muted: "#8d99a8",
    faint: "#5c6875",
    ok: "#34d399",
    warn: "#fbbf24",
    down: "#f87171",
    pill: "rgba(255,255,255,0.06)",
    chartPalette: ["#C8FF00", "#5B9BFF", "#E0B45C", "#E27B78", "#B08FFF", "#9DA3A6"],
  },
  light: {
    bgGradient: "linear-gradient(165deg, #f7f8fa 0%, #ffffff 60%, #f4f6f8 100%)",
    panelBorder: "1px solid rgba(13,22,31,0.10)",
    divider: "1px solid rgba(13,22,31,0.08)",
    gridLine: "rgba(13,22,31,0.07)",
    text: "#212a33",
    muted: "#5d6a77",
    faint: "#8a939c",
    ok: "#86c200",
    warn: "#d9a013",
    down: "#d95653",
    pill: "rgba(13,22,31,0.05)",
    // 与暗色同构的六色系，明度压到白底可读（首色对应亮色 --chart-ok）
    chartPalette: ["#7fb800", "#3d7bd9", "#c09043", "#d95653", "#9a6ff0", "#6b7480"],
  },
};

/** 延迟折线系列色：与站内图表色板同源，随分享图主题取对应一套。 */
function chartPalette(theme: ShareTheme): string[] {
  return SHARE_PALETTES[theme].chartPalette;
}

const RATE_LEVEL_LABELS: Record<RateLevel, string> = {
  ok: "≥80% 优秀",
  warn: "60–80% 警告",
  down: "<60% 不及格",
};

const CHART_W = 864;
const CHART_H = 150;

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

/** 可用率阶梯图路径：每档一条折线 + 填充；段外多带一个邻点让相邻段首尾相接。 */
function availabilityPaths(points: AvailabilityPoint[], w: number, h: number) {
  const x = (i: number) => (i / (points.length - 1)) * w;
  const y = (pct: number) => 8 + (1 - pct / 100) * (h - 14);
  const levels = points.map((point) => rateLevel(point.pct));
  return levelRuns(levels).map((run) => {
    const from = Math.max(run.start - 1, 0);
    const to = Math.min(run.end + 1, points.length - 1);
    let line = "";
    for (let i = from; i <= to; i += 1) {
      line += i === from ? `M ${x(i)} ${y(points[i].pct)}` : ` L ${x(i)} ${y(points[i].pct)}`;
      if (i < to) line += ` L ${x(i + 1)} ${y(points[i].pct)}`;
    }
    return { level: run.level, line, area: `${line} L ${x(to)} ${h} L ${x(from)} ${h} Z` };
  });
}

/** 分享图 · 可用率趋势：阶梯面积图按三档着色，与站点状态页趋势图同口径。 */
function ShareTrendChart({ points, theme }: { points: AvailabilityPoint[]; theme: ShareTheme }) {
  const c = SHARE_PALETTES[theme];
  const rateColors: Record<RateLevel, string> = { ok: c.ok, warn: c.warn, down: c.down };
  const runs = availabilityPaths(points, CHART_W, CHART_H);
  const avg = Math.round(points.reduce((sum, point) => sum + point.pct, 0) / points.length);
  return (
    <div style={{ border: c.panelBorder, borderRadius: 14, padding: "18px 20px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span style={{ fontSize: 14.5, fontWeight: 600 }}>可用渠道占比趋势 · 最近 7 天</span>
        <span style={{ fontFamily: MONO, fontSize: 12.5, color: c.muted }}>7 日均值 {avg}%</span>
      </div>
      <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} width="100%" role="img" aria-label="可用渠道占比趋势图">
        {[0, 50, 100].map((v) => {
          const gy = 8 + (1 - v / 100) * (CHART_H - 14);
          return (
            <g key={v}>
              <line x1={0} x2={CHART_W} y1={gy} y2={gy} stroke={c.gridLine} />
              <text x={2} y={gy - 4} fill={c.faint} fontSize={10} fontFamily={MONO}>
                {v}%
              </text>
            </g>
          );
        })}
        {runs.map((run, index) => (
          <g key={index}>
            <path d={run.area} fill={rateColors[run.level]} opacity={0.1} />
            <path d={run.line} fill="none" stroke={rateColors[run.level]} strokeWidth={1.5} />
          </g>
        ))}
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

/** 渠道延迟折线路径：每个渠道一条，缺席检测点的渠道不连线。 */
function latencyPaths(points: LatencyPoint[], names: string[], palette: string[]) {
  const max = Math.max(...points.flatMap((point) => Object.values(point.values)), 1) * 1.05;
  const x = (i: number) => (i / (points.length - 1)) * CHART_W;
  const y = (v: number) => 8 + (1 - v / max) * (CHART_H - 14);
  return names
    .map((name, index) => {
      const parts = points
        .map((point, i) => (point.values[name] != null ? `${x(i)},${y(point.values[name])}` : null))
        .filter((value): value is string => value != null);
      if (parts.length === 0) return null;
      return { name, color: palette[index % palette.length], line: `M ${parts.join(" L ")}` };
    })
    .filter((line): line is { name: string; color: string; line: string } => line != null);
}

/** 分享图 · 渠道延迟趋势：每渠道一条折线（ms），与站点状态页延迟图同口径。 */
function ShareLatencyChart({ points, names, theme }: { points: LatencyPoint[]; names: string[]; theme: ShareTheme }) {
  const c = SHARE_PALETTES[theme];
  const palette = chartPalette(theme);
  const lines = latencyPaths(points, names, palette).slice(0, palette.length);
  const values = points.flatMap((point) => Object.values(point.values));
  const avg = Math.round(values.reduce((sum, v) => sum + v, 0) / values.length);
  return (
    <div style={{ border: c.panelBorder, borderRadius: 14, padding: "18px 20px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span style={{ fontSize: 14.5, fontWeight: 600 }}>渠道延迟趋势 · 最近 7 天</span>
        <span style={{ fontFamily: MONO, fontSize: 12.5, color: c.muted }}>均值 {avg}ms</span>
      </div>
      <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} width="100%" role="img" aria-label="渠道延迟趋势图">
        <line x1={0} x2={CHART_W} y1={CHART_H - 6} y2={CHART_H - 6} stroke={c.gridLine} />
        {lines.map((line) => (
          <path key={line.name} d={line.line} fill="none" stroke={line.color} strokeWidth={1.5} strokeLinejoin="round" />
        ))}
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

/** 中转站实时状况分享卡：纯 DOM、固定 1000px 宽、全部内联样式，
 *  由 ShareSiteButton 用 html-to-image 栅格化成 PNG；支持亮/暗两套配色。 */
export function ShareSiteCard({
  siteName,
  homepage,
  domain,
  availability,
  channels,
  latency,
  latencyNames,
  generatedAt,
  theme = "dark",
}: {
  siteName: string;
  homepage: string;
  /** 监控站自己的域名，用于分享回流 */
  domain: string;
  availability: AvailabilityPoint[];
  channels: ChannelDotRow[];
  latency: LatencyPoint[];
  latencyNames: string[];
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
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", border: c.panelBorder, borderRadius: 14, overflow: "hidden" }}>
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
          异常渠道：{downNames.join("、")}
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

      {/* 趋势图：可用率必有（≥2 个检测点），延迟图仅有渠道带延迟时出现 */}
      {availability.length >= 2 && <ShareTrendChart points={availability} theme={theme} />}
      {latencyNames.length > 0 && latency.length >= 2 && <ShareLatencyChart points={latency} names={latencyNames} theme={theme} />}

      {/* 渠道状态列表 */}
      {shown.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column" }}>
          {shown.map((channel, index) => {
            const last = channel.dots[channel.dots.length - 1];
            const latency = last?.latency != null ? `${last.latency}ms` : null;
            const availability7d = channel.availability7d != null ? `${channel.availability7d.toFixed(2)}%` : null;
            const metrics = [latency, availability7d].filter(Boolean).join(" · ");
            return (
              <div
                key={channel.name}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 16,
                  padding: "11px 4px",
                  borderTop: index === 0 ? c.divider : "none",
                  borderBottom: index < shown.length - 1 || hiddenCount > 0 ? c.divider : "none",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
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
