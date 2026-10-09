import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { apiGet } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { ToneTag } from "@/components/ToneTag";
import { StatusCharts } from "@/components/StatusCharts";
import { formatTime } from "@/lib/format";
import { availabilityBySite, channelDotsBySite, latencyBySite } from "@/lib/channelStatus";
import { getSiteInfo } from "@/lib/sites";
import type { NoticeSnapshot, StatusChange, StatusEvent, StatusSnapshot } from "@/lib/types";

export const dynamic = "force-dynamic";

export const metadata = { title: "站点检测" };

const PARSE_LABELS: Record<string, string> = {
  json: "JSON",
  embedded_json: "页面内嵌 JSON",
  ai: "AI 解析",
  text: "纯文本",
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

/** 时间戳类字段轮询必变（与后端 diff_status 的跳过口径一致），变化行不渲染。 */
const TIME_KEY_RE = /(^|_)(at|time|ts)$|^(time|timestamp|ts|updated|datetime|last_update)$/i;

function isVolatilePath(path: string): boolean {
  const leaf = (path.split(/[.[]/).pop() ?? "").replace(/\]\.?$/, "");
  return TIME_KEY_RE.test(leaf);
}

function formatChangeValue(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  if (typeof value === "boolean") return String(value);
  const text = typeof value === "object" ? JSON.stringify(value) : String(value);
  return text.length > 120 ? `${text.slice(0, 120)}…` : text;
}

function ChangeLine({ change }: { change: StatusChange }) {
  const target = change.op === "remove" ? change.old : change.new;
  const text = formatChangeValue(target);
  // 数值四舍五入到 2 位后没变的（如 availability 抖动）不算有效变化
  if (change.op === "change" && text === formatChangeValue(change.old)) return null;
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
      <span
        className="mono"
        style={{
          fontSize: 12,
          padding: "1px 6px",
          borderRadius: 6,
          background: change.op === "remove" ? "var(--tone-red-bg, rgba(200,60,60,.12))" : "var(--hover)",
        }}
      >
        {change.op}
      </span>
      <span className="mono" style={{ fontSize: 12.5 }}>
        {change.path.replace(/^\$\.?/, "")}
      </span>
      {target !== undefined && (
        <span style={{ fontSize: 12.5, color: "var(--text-2)", overflow: "hidden", textOverflow: "ellipsis", maxWidth: "min(480px, 100%)", minWidth: 0, whiteSpace: "nowrap" }}>
          {text}
        </span>
      )}
    </div>
  );
}

export default async function StatusDetailPage({ params }: { params: Promise<{ siteId: string }> }) {
  const { siteId: rawId } = await params;
  const siteId = decodeURIComponent(rawId);

  let records: StatusSnapshot[] = [];
  let eventList: StatusEvent[] = [];
  let notices: NoticeSnapshot[] = [];
  let error: string | null = null;
  try {
    const [timeline, events, noticeData] = await Promise.all([
      apiGet<{ records: StatusSnapshot[]; total: number }>(`/api/status?site_id=${encodeURIComponent(siteId)}&limit=200`),
      apiGet<{ events: StatusEvent[] }>(`/api/status/events?site_id=${encodeURIComponent(siteId)}&limit=50`).catch(
        () => ({ events: [] as StatusEvent[] }),
      ),
      // 公告拉取失败只影响公告区，不阻塞整页
      apiGet<{ records: NoticeSnapshot[] }>(`/api/notice?site_id=${encodeURIComponent(siteId)}&limit=50`).catch(() => ({
        records: [] as NoticeSnapshot[],
      })),
    ]);
    records = timeline.records ?? [];
    eventList = events.events ?? [];
    // 存储按时间升序返回，公告区最新版本在前
    notices = (noticeData.records ?? []).slice().reverse();
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
          <section className="panel" style={{ display: "grid", gap: 4, padding: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 8, paddingBottom: 2 }}>
              <h3 className="section-title">渠道检测趋势</h3>
              <span style={{ fontSize: 12, color: "var(--text-3)" }}>
                {series.length} 个检测点{firstAt ? ` · ${formatTime(firstAt)} → ${formatTime(lastAt)}` : ""}
              </span>
            </div>
            <StatusCharts availability={series} latency={latencySeries} latencyNames={latencyNames} showLatency={showLatencyTrend} />
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
                    <span className="mono ch-lat">{metrics.join(" · ") || "—"}</span>
                    <span className="mono ch-rate">{okCount}/{channel.dots.length}</span>
                  </div>
                );
              })}
            </section>
          )}

          {notices.length > 0 && (
            <section className="panel" style={{ display: "grid", gap: 8, padding: 16 }}>
              <h3 className="section-title">站点公告</h3>
              <span style={{ fontSize: 12, color: "var(--text-3)" }}>
                公告内容变化时才记一条版本，最新在前；正文按站点原文渲染。
              </span>
              {notices.map((notice, index) => (
                <details key={index} open={index === 0} style={{ borderTop: "1px solid var(--border)", paddingTop: 8 }}>
                  <summary style={{ cursor: "pointer", fontSize: 13 }}>
                    <span className="mono" style={{ color: "var(--text-2)" }}>{formatTime(notice.captured_at)}</span>
                    <span style={{ color: "var(--text-3)", fontSize: 12, marginLeft: 8 }}>
                      {index === 0 ? "当前版本" : "历史版本"} · {PARSE_LABELS[notice.parse] ?? notice.parse}
                    </span>
                  </summary>
                  <div style={{ fontSize: 13.5, lineHeight: 1.65, padding: "10px 2px 2px" }}>
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{notice.content}</ReactMarkdown>
                  </div>
                </details>
              ))}
              <span className="mono" style={{ fontSize: 12, color: "var(--text-3)" }}>
                来源：{notices[0]?.source_url ?? "—"}
              </span>
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

          {eventList.length > 0 && (
            <section className="panel" style={{ display: "grid", gap: 10, padding: 16 }}>
              <h3 className="section-title">最近变化</h3>
              {eventList
                .map((event, index) => ({
                  key: index,
                  kind: event.kind === "status_init" ? "首次建档" : "内容变化",
                  detectedAt: event.detected_at,
                  // 时间戳类字段轮询必变（旧事件已入库），展示层兜底过滤；全被过滤的纯噪音事件整条隐藏
                  changes: event.changes.filter((change) => !isVolatilePath(change.path)),
                }))
                .filter((item) => item.changes.length > 0)
                .map(({ key, kind, detectedAt, changes }) => (
                  <div key={key} style={{ display: "grid", gap: 6, padding: "8px 0", borderTop: "1px solid var(--border)" }}>
                    <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "baseline" }}>
                      <span style={{ fontSize: 13, fontWeight: 550 }}>{kind}</span>
                      <span className="mono" style={{ fontSize: 12, color: "var(--text-3)" }}>
                        {formatTime(detectedAt)}
                      </span>
                    </div>
                    {changes.map((change, changeIndex) => (
                      <ChangeLine key={changeIndex} change={change} />
                    ))}
                  </div>
                ))}
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
