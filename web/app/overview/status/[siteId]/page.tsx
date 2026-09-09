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
import { ShareSiteButton } from "@/components/ShareSiteButton";
import { formatTime } from "@/lib/format";
import {
  buildSiteViews,
  buildUptimeBuckets,
  latencyLevel,
  rateLevel,
  type RateLevel,
} from "@/lib/channelStatus";
import { getSiteInfo } from "@/lib/sites";
import { subtitles } from "@/lib/copy";
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

const RATE_TONE: Record<RateLevel, string> = {
  ok: "var(--tone-green-text)",
  warn: "var(--tone-yellow-text)",
  down: "var(--tone-red-text)",
};

/** 站点公告区：始终展示最新一次采集的内容（该内容已包含站点当前的公告列表） */
function NoticeSection({ notices }: { notices: NoticeSnapshot[] }) {
  if (notices.length === 0) return null;
  const notice = notices[0];
  return (
    <section className="panel" style={{ display: "grid", gap: 8, padding: 16 }}>
      <h3 className="section-title">站点公告</h3>
      <span className="mono" style={{ fontSize: 12, color: "var(--text-3)" }}>
        {formatTime(notice.captured_at)} 采集 · 来源：{notice.source_url ?? "—"}
      </span>
      <div className="notice-body" style={{ fontSize: 13.5, lineHeight: 1.65 }}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[rehypeRaw, [rehypeSanitize, NOTICE_SANITIZE_SCHEMA]]}
        >
          {notice.content}
        </ReactMarkdown>
      </div>
    </section>
  );
}

export default async function StatusDetailPage({
  params,
}: {
  params: Promise<{ siteId: string }>;
}) {
  const { siteId: rawId } = await params;
  const siteId = decodeURIComponent(rawId);

  let records: StatusSnapshot[] = [];
  let notices: NoticeSnapshot[] = [];
  let error: string | null = null;
  try {
    // 固定看最近 7 天：时间条件下推给接口（since，数据库只取范围内的行）。
    // 库里的快照只存时间线增量（写入侧 strip_status_delta 裁掉与上一条重复的检测点），
    // 单条很小；max_records 只作为极密站点的防御上限——抽样会打洞，不能设太小。
    const since = Math.floor(Date.now() / 1000 - 7 * 86_400);
    const [timeline, noticeData] = await Promise.all([
      apiGet<{ records: StatusSnapshot[]; total: number }>(
        `/api/status?site_id=${encodeURIComponent(siteId)}&limit=2000&since=${since}&max_records=400`,
      ),
      // 公告拉取失败只影响公告区，不阻塞整页；只取最新一次采集的存档
      apiGet<{ records: NoticeSnapshot[] }>(
        `/api/notice?site_id=${encodeURIComponent(siteId)}&limit=1`,
      ).catch(() => ({
        records: [] as NoticeSnapshot[],
      })),
    ]);
    records = timeline.records ?? [];
    notices = noticeData.records ?? [];
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }

  const views = buildSiteViews(records);
  const series = views.availability[siteId] ?? [];
  const channels = views.channels[siteId] ?? [];
  const latencySeries = views.latency[siteId] ?? [];
  const latencyNames = channels
    .filter((channel) => channel.dots.some((dot) => dot.latency != null))
    .map((channel) => channel.name);
  // 顶部整站时段色块：同样服务端预分桶，客户端只拿 ~28 个桶
  const uptimeBuckets = buildUptimeBuckets(series, 28);

  return (
    <div className="page">
      <PageHeader
        eyebrow="SITE CHECK"
        title={`站点检测 · ${getSiteInfo(siteId).name || siteId}`}
        subtitle={subtitles.siteStatus}
        actions={
          records.length > 0 ? (
            <ShareSiteButton
              siteName={getSiteInfo(siteId).name || siteId}
              homepage={getSiteInfo(siteId).homepage}
              availability={series}
              channels={channels}
              latency={latencySeries}
              latencyNames={latencyNames}
            />
          ) : undefined
        }
      />
      {error ? (
        <p style={{ color: "var(--tone-red-text)" }}>加载失败：{error}</p>
      ) : (
        <div style={{ display: "grid", gap: 16 }}>
          {records.length === 0 && (
            <p style={{ color: "var(--text-3)" }}>
              这个中转站最近 7 天的检测档案：可用渠道占比趋势、各渠道当前状态与
              站点公告；绿色为正常、灰色为异常或未知。
            </p>
          )}
          {records.length > 0 && (
            <>
              <section
                className="panel"
                style={{ display: "grid", gap: 14, padding: 16 }}
              >
                <StatusUptimeBars buckets={uptimeBuckets} />
                <div style={{ paddingTop: 4 }}>
                  <StatusCharts availability={series} channels={channels} />
                </div>
              </section>

              {channels.length > 0 && (
                <section
                  className="panel"
                  style={{ display: "grid", gap: 2, padding: 16 }}
                >
                  <h3 className="section-title" style={{ paddingBottom: 6 }}>
                    渠道当前状态
                  </h3>
                  <div className="ch-row ch-head">
                    <span>渠道</span>
                    <span>状态</span>
                    <span className="ch-lat">延迟 · 7日可用率</span>
                    <span className="ch-upt">可用记录</span>
                    <span className="ch-rate">正常次数</span>
                  </div>
                  {channels.map((channel) => {
                    const last = channel.dots[channel.dots.length - 1];
                    const okCount = channel.dots.filter((dot) => dot.ok).length;
                    const metrics = [
                      last?.latency != null ? `${last.latency}ms` : null,
                      channel.availability7d != null
                        ? `${channel.availability7d.toFixed(2)}%`
                        : null,
                    ].filter(Boolean);
                    // 该渠道（分组）自己的可用率时段桶：每次检测正常记 100%、异常记 0%，服务端分好桶再传给组件
                    const uptimeBuckets = buildUptimeBuckets(
                      channel.dots
                        .filter((dot) => dot.at != null)
                        .map((dot) => ({
                          at: dot.at as number,
                          pct: dot.ok ? 100 : 0,
                          down: dot.ok ? [] : [channel.name],
                        })),
                    );
                    return (
                      <div key={channel.name} className="ch-row">
                        <span className="ch-name">
                          <span className="mono">{channel.name}</span>
                          {(channel.provider || channel.model) && (
                            <span className="ch-sub">
                              {[channel.provider, channel.model]
                                .filter(Boolean)
                                .join(" · ")}
                            </span>
                          )}
                        </span>
                        <ToneTag tone={last?.ok ? "green" : "gray"}>
                          {last?.status ?? "unknown"}
                        </ToneTag>
                        <span
                          className="mono ch-lat"
                          style={
                            last?.latency != null
                              ? { color: RATE_TONE[latencyLevel(last.latency)] }
                              : channel.availability7d != null
                                ? {
                                    color:
                                      RATE_TONE[
                                        rateLevel(channel.availability7d)
                                      ],
                                  }
                                : undefined
                          }
                        >
                          {metrics.join(" · ") || "—"}
                        </span>
                        <span className="ch-upt">
                          <StatusUptimeBars buckets={uptimeBuckets} />
                        </span>
                        <span className="mono ch-rate">
                          {okCount}/{channel.dots.length}
                        </span>
                      </div>
                    );
                  })}
                </section>
              )}
            </>
          )}
          <NoticeSection notices={notices} />

          <Link
            href="/overview"
            className="landing-more"
            style={{ display: "inline-block" }}
          >
            ← 返回中转站定价
          </Link>
        </div>
      )}
    </div>
  );
}
