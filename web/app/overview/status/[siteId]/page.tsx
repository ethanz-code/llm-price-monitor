import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import { apiGet } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { ToneTag } from "@/components/ToneTag";
import { StatusCharts } from "@/components/StatusCharts";
import { StatusUptimeBars } from "@/components/StatusUptimeBars";
import { formatTime } from "@/lib/format";
import { availabilityBySite, channelDotsBySite, latencyBySite, latencyLevel, rateLevel, type RateLevel } from "@/lib/channelStatus";
import { getSiteInfo } from "@/lib/sites";
import { StatCard } from "@/components/PageHeader";
import type { NoticeSnapshot, StatusSnapshot } from "@/lib/types";

export const dynamic = "force-dynamic";

export const metadata = { title: "站点检测" };

/**
 * 公告正文允许的 HTML 白名单：站点公告常带 HTML 片段，经 rehype-raw 渲染；
 * 白名单在默认基础上放开 class，保住站点的排版样式，脚本等危险内容仍被剥掉。
 */
const NOTICE_SANITIZE_SCHEMA = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    "*": [...(defaultSchema.attributes?.["*"] ?? []), "className"],
  },
};

const PARSE_LABELS: Record<string, string> = {
  json: "JSON",
  embedded_json: "页面内嵌 JSON",
  ai: "AI 解析",
  text: "纯文本",
};

/** 可用率档位 → KPI 数字的语义色，与趋势图的分段着色一致。 */
const RATE_TONE: Record<RateLevel, string> = {
  ok: "var(--tone-green-text)",
  warn: "var(--tone-yellow-text)",
  down: "var(--tone-red-text)",
};

function ValueNode({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span style={{ color: "var(--text-3)" }}>null</span>;
  if (typeof value === "boolean" || typeof value === "number") {
    return <span className="mono">{String(value)}</span>;
  }
  // 长无空格串（URL/哈希）不撑破窄屏容器
  if (typeof value === "string") return <span style={{ overflowWrap: "anywhere" }}>{value}</span>;
  if (Array.isArray(value)) {
    if (value.length === 0) return <span style={{ color: "var(--text-3)" }}>[]</span>;
    return (
      <div style={{ display: "grid", gap: 4 }}>
        {value.map((item, index) => (
          <div key={index} style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
            <span className="mono" style={{ fontSize: 12, color: "var(--text-3)", minWidth: 28 }}>
              [{index}]
            </span>
            <ValueNode value={item} />
          </div>
        ))}
      </div>
    );
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return <span style={{ color: "var(--text-3)" }}>{"{}"}</span>;
    return (
      <div style={{ display: "grid", gap: 4 }}>
        {entries.map(([key, child]) => (
          <div key={key} style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
            <span className="mono" style={{ fontSize: 12, color: "var(--text-3)", minWidth: 96 }}>
              {key}
            </span>
            <ValueNode value={child} />
          </div>
        ))}
      </div>
    );
  }
  return <span className="mono">{String(value)}</span>;
}

export default async function StatusDetailPage({ params }: { params: Promise<{ siteId: string }> }) {
  const { siteId: rawId } = await params;
  const siteId = decodeURIComponent(rawId);

  let records: StatusSnapshot[] = [];
  let notices: NoticeSnapshot[] = [];
  let error: string | null = null;
  try {
    const [timeline, noticeData] = await Promise.all([
      apiGet<{ records: StatusSnapshot[]; total: number }>(`/api/status?site_id=${encodeURIComponent(siteId)}&limit=200`),
      // 公告拉取失败只影响公告区，不阻塞整页
      apiGet<{ records: NoticeSnapshot[] }>(`/api/notice?site_id=${encodeURIComponent(siteId)}&limit=1`).catch(() => ({
        records: [] as NoticeSnapshot[],
      })),
    ]);
    records = timeline.records ?? [];
    // 只展示最新一条公告，不做历史版本
    notices = noticeData.records ?? [];
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }

  const series = availabilityBySite(records)[siteId] ?? [];
  const channels = channelDotsBySite(records)[siteId] ?? [];
  const latencySeries = latencyBySite(records)[siteId] ?? [];
  const latencyNames = channels.filter((channel) => channel.dots.some((dot) => dot.latency != null)).map((channel) => channel.name);
  const showLatencyTrend = latencyNames.length > 0 && latencySeries.length >= 2;
  const trendFootnote = `每个点代表一次渠道检测（站点逐分钟自检时即为一分钟）${showLatencyTrend ? " · 延迟为渠道自报的检测耗时" : ""}`;
  const latest = records.reduce<StatusSnapshot | undefined>(
    (acc, row) => (acc === undefined || row.captured_at > acc.captured_at ? row : acc),
    undefined,
  );
  const firstAt = series[0]?.at;
  const lastAt = series[series.length - 1]?.at;
  // KPI：可用率取最后一个检测点；延迟 / 自报可用率取各渠道最近一次的均值
  const lastPct = series[series.length - 1]?.pct;
  const downCount = channels.filter((channel) => !channel.ok).length;
  const latestLatencies = channels
    .map((channel) => channel.dots[channel.dots.length - 1]?.latency)
    .filter((value): value is number => value != null);
  const avgLatency = latestLatencies.length
    ? Math.round(latestLatencies.reduce((sum, value) => sum + value, 0) / latestLatencies.length)
    : null;
  const selfReported = channels.map((channel) => channel.availability7d).filter((value): value is number => value != null);
  const avgSelfReported = selfReported.length
    ? Math.round(selfReported.reduce((sum, value) => sum + value, 0) / selfReported.length)
    : null;

  return (
    <div className="page">
      <PageHeader
        eyebrow="SITE CHECK"
        title={`站点检测 · ${getSiteInfo(siteId).name || siteId}`}
        subtitle="这个中转站的检测档案：渠道可用率趋势、各渠道当前状态、站点公告的历史版本；绿色为正常、灰色为异常或未知。"
      />
      {error ? (
        <p style={{ color: "var(--tone-red-text)" }}>加载失败：{error}</p>
      ) : records.length === 0 ? (
        <p style={{ color: "var(--text-3)" }}>
          该站点还没有渠道状态存档。在管理后台的站点表单里填写「渠道状态 URL」后，采集时会顺带拉取并存档。
        </p>
      ) : (
        <div style={{ display: "grid", gap: 16 }}>
          <div className="stat-grid">
            <StatCard
              label="当前可用率"
              value={lastPct != null ? <span style={{ color: RATE_TONE[rateLevel(lastPct)] }}>{lastPct}%</span> : "—"}
              hint="最近一次检测的正常渠道占比"
            />
            <StatCard
              label="异常渠道"
              value={
                <span style={downCount > 0 ? { color: "var(--tone-red-text)" } : undefined}>
                  {downCount}
                  <span style={{ fontSize: 15, color: "var(--text-3)" }}> / {channels.length}</span>
                </span>
              }
              hint="最近一次检测状态异常的渠道数"
            />
            <StatCard
              label="平均延迟"
              value={
                avgLatency != null ? (
                  <span style={{ color: RATE_TONE[latencyLevel(avgLatency)] }}>{avgLatency}ms</span>
                ) : (
                  "—"
                )
              }
              hint="渠道自报 · 最近一次检测；≥1s 偏慢、≥3s 过高"
            />
            <StatCard
              label="7日自报可用率"
              value={avgSelfReported != null ? `${avgSelfReported}%` : "—"}
              hint="站点自己报的数，仅供参考"
            />
          </div>
          <section className="panel" style={{ display: "grid", gap: 14, padding: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
              <h3 className="section-title">渠道检测趋势</h3>
              <span style={{ fontSize: 12, color: "var(--text-3)" }}>
                {series.length} 个检测点{firstAt ? ` · ${formatTime(firstAt)} → ${formatTime(lastAt)}` : ""}
              </span>
            </div>
            <StatusUptimeBars points={series} />
            <div style={{ paddingTop: 4 }}>
              <StatusCharts availability={series} latency={latencySeries} latencyNames={latencyNames} showLatency={showLatencyTrend} />
            </div>
            <span className="mono" style={{ fontSize: 12, color: "var(--text-3)", paddingTop: 8, overflowWrap: "anywhere" }}>
              {trendFootnote} · 来源：
              {latest?.source_url ?? "—"} · 最近检测 {latest ? formatTime(latest.captured_at) : "—"}
              {latest ? ` · HTTP ${latest.http_status} · ${PARSE_LABELS[latest.parse] ?? latest.parse}` : ""}
            </span>
          </section>

          {channels.length > 0 && (
            <section className="panel" style={{ display: "grid", gap: 2, padding: 16 }}>
              <h3 className="section-title" style={{ paddingBottom: 6 }}>渠道当前状态</h3>
              <div className="ch-row ch-head">
                <span>渠道</span>
                <span>状态</span>
                <span className="ch-lat">延迟 · 7日可用率</span>
                <span className="ch-rate">正常次数</span>
              </div>
              {channels.map((channel) => {
                const last = channel.dots[channel.dots.length - 1];
                const okCount = channel.dots.filter((dot) => dot.ok).length;
                const metrics = [
                  last?.latency != null ? `${last.latency}ms` : null,
                  channel.availability7d != null ? `${channel.availability7d}%` : null,
                ].filter(Boolean);
                return (
                  <div key={channel.name} className="ch-row">
                    <span className="ch-name">
                      <span className="mono">{channel.name}</span>
                      {(channel.provider || channel.model) && (
                        <span className="ch-sub">{[channel.provider, channel.model].filter(Boolean).join(" · ")}</span>
                      )}
                    </span>
                    <ToneTag tone={last?.ok ? "green" : "gray"}>{last?.status ?? "unknown"}</ToneTag>
                    <span
                      className="mono ch-lat"
                      style={
                        last?.latency != null
                          ? { color: RATE_TONE[latencyLevel(last.latency)] }
                          : channel.availability7d != null
                            ? { color: RATE_TONE[rateLevel(channel.availability7d)] }
                            : undefined
                      }
                    >
                      {metrics.join(" · ") || "—"}
                    </span>
                    <span className="mono ch-rate">{okCount}/{channel.dots.length}</span>
                  </div>
                );
              })}
            </section>
          )}

          {notices.length > 0 && (
            <section className="panel" style={{ display: "grid", gap: 8, padding: 16 }}>
              <h3 className="section-title">站点公告</h3>
              <span className="mono" style={{ fontSize: 12, color: "var(--text-3)" }}>
                {formatTime(notices[0].captured_at)} · 来源：{notices[0].source_url ?? "—"}
              </span>
              <div className="notice-body" style={{ fontSize: 13.5, lineHeight: 1.65 }}>
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  rehypePlugins={[rehypeRaw, [rehypeSanitize, NOTICE_SANITIZE_SCHEMA]]}
                >
                  {notices[0].content}
                </ReactMarkdown>
              </div>
            </section>
          )}

          {latest && (
            <section className="panel" style={{ padding: 16 }}>
              <details>
                <summary style={{ cursor: "pointer", fontSize: 14.5, fontWeight: 550 }}>最近一次原始数据</summary>
                <div style={{ marginTop: 10 }}>
                  <ValueNode value={latest.data} />
                </div>
              </details>
            </section>
          )}

          <Link href="/overview" className="landing-more" style={{ display: "inline-block" }}>
            ← 返回中转站定价
          </Link>
        </div>
      )}
    </div>
  );
}
