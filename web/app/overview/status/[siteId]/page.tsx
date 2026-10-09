import Link from "next/link";
import { apiGet } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { NoticeBody } from "@/components/NoticeBody";
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
  successRateLevel,
  type RateLevel,
} from "@/lib/channelStatus";
import type { Tone } from "@/components/ToneTag";
import { getSiteInfo } from "@/lib/sites";
import type { NoticeSnapshot, StatusSnapshot } from "@/lib/types";

export const dynamic = "force-dynamic";

export const metadata = { title: "站点检测" };

const RATE_TONE: Record<RateLevel, string> = {
  ok: "var(--tone-green-text)",
  warn: "var(--tone-yellow-text)",
  down: "var(--tone-red-text)",
};

/** 指标型渠道（无状态词、以成功率表达健康度）状态标签的三档着色 */
const RATE_TAG_TONE: Record<RateLevel, Tone> = {
  ok: "green",
  warn: "yellow",
  down: "red",
};

/** 从标题文字里抠出日期（兼容全角/半角括号），顺便把日期从展示标题里去掉 */
function extractDate(heading: string): { clean: string; date: string | null } {
  const match = /(20\d{2}-\d{2}-\d{2})/.exec(heading);
  const clean = heading
    .replace(/[（(]\s*20\d{2}-\d{2}-\d{2}\s*[)）]/g, "")
    .replace(/20\d{2}-\d{2}-\d{2}/, "")
    .replace(/[\s·、\-–]+$/, "")
    .trim();
  return { clean, date: match?.[1] ?? null };
}

/** 按 markdown 标题行把整块公告内容拆成一条条（站方习惯每条公告一个标题 + 日期）；
 *  拆不出第二条（没有标题结构）就返回单条，UI 退回整块渲染。 */
function splitNoticeEntries(content: string): { clean: string; date: string | null; body: string }[] {
  const entries: { clean: string; date: string | null; body: string }[] = [];
  let current: { clean: string; date: string | null; body: string } | null = null;
  for (const line of content.split("\n")) {
    const heading = /^(#{1,4})\s+(.+?)\s*$/.exec(line);
    if (heading) {
      if (current) entries.push(current);
      const { clean, date } = extractDate(heading[2]);
      current = { clean, date, body: "" };
    } else if (current) {
      current.body += `${line}\n`;
    }
  }
  if (current) entries.push(current);
  return entries;
}

/** 站点公告区：展示最新一次采集的内容（该内容已包含站点当前的公告列表）。
 *  默认展开最新一条，更早的折叠进 details，避免历史公告把页面拖到数屏长。 */
function NoticeSection({ notices }: { notices: NoticeSnapshot[] }) {
  if (notices.length === 0) return null;
  const notice = notices[0];
  // 内容为空（站点没发布或公告被清空）就不渲染整块，免得只剩一行采集时间
  if (!notice.content.trim()) return null;
  // 来源只展示域名，完整地址留在链接里；存的地址解析失败就只显示采集时间
  let sourceHost = "";
  try {
    sourceHost = new URL(notice.source_url).host;
  } catch {
    sourceHost = "";
  }

  const entries = splitNoticeEntries(notice.content);
  // 拆不出结构（不足两条）就整块渲染，交给 NoticeBody
  if (entries.length < 2) {
    return (
      <section className="panel notice-section">
        <NoticeMeta capturedAt={formatTime(notice.captured_at)} sourceHost={sourceHost} sourceUrl={notice.source_url} />
        <NoticeBody content={notice.content} />
      </section>
    );
  }

  const [latest, ...older] = entries;
  const oldestDate = older[older.length - 1]?.date;
  const rangeText =
    oldestDate && older[0]?.date ? `（${older[0].date} ~ ${oldestDate}）` : "";

  return (
    <section className="panel notice-section">
      <NoticeMeta capturedAt={formatTime(notice.captured_at)} sourceHost={sourceHost} sourceUrl={notice.source_url} />

      <article className="notice-item">
        <div className="notice-item-head">
          <h4 className="notice-item-title">{latest.clean}</h4>
          {latest.date && <span className="mono notice-item-date">{latest.date}</span>}
        </div>
        <NoticeBody content={latest.body} />
      </article>

      {older.length > 0 && (
        <details className="notice-history">
          <summary>
            更早的公告 · {older.length} 条
            {rangeText && <span className="notice-history-range">{rangeText}</span>}
          </summary>
          <div>
            {older.map((entry, index) => (
              <article className="notice-item" key={`${entry.date ?? "entry"}-${index}`}>
                <div className="notice-item-head">
                  <h4 className="notice-item-title">{entry.clean}</h4>
                  {entry.date && <span className="mono notice-item-date">{entry.date}</span>}
                </div>
                <NoticeBody content={entry.body} />
              </article>
            ))}
          </div>
        </details>
      )}
    </section>
  );
}

/** 公告区头部：标题 + 采集时间 + 来源域名 */
function NoticeMeta({
  capturedAt,
  sourceHost,
  sourceUrl,
}: {
  capturedAt: string;
  sourceHost: string;
  sourceUrl: string;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "baseline",
        gap: "2px 12px",
      }}
    >
      <h3 className="section-title">站点公告</h3>
      <span className="mono" style={{ fontSize: 12, color: "var(--text-3)" }}>
        {capturedAt} 采集
        {sourceHost && (
          <>
            {" · 来源 "}
            <a
              className="notice-src"
              href={sourceUrl}
              target="_blank"
              rel="noreferrer"
              title={sourceUrl}
            >
              {sourceHost}
            </a>
          </>
        )}
      </span>
    </div>
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
  // 顶部整站时段色块：同样服务端预分桶，客户端只拿 ~28 个桶
  const uptimeBuckets = buildUptimeBuckets(series, 28);

  return (
    <div className="page">
      <PageHeader
        title={`站点检测 · ${getSiteInfo(siteId).name || siteId}`}
        actions={
          records.length > 0 ? (
            <ShareSiteButton
              siteName={getSiteInfo(siteId).name || siteId}
              homepage={getSiteInfo(siteId).homepage}
              availability={series}
              channels={channels}
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
              这个站点还没有检测记录，等下一轮检测出结果再来看看。
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
                    <span className="ch-metrics">成功率 · 出字速度</span>
                    <span className="ch-upt">可用记录</span>
                    <span className="ch-rate">正常次数</span>
                  </div>
                  {channels.map((channel) => {
                    const last = channel.dots[channel.dots.length - 1];
                    const okCount = channel.dots.filter((dot) => dot.ok).length;
                    // 指标型渠道：状态标签直接展示成功率并按三档着色，附最近几轮走势小字
                    const metricTone =
                      last?.rate != null
                        ? RATE_TAG_TONE[successRateLevel(last.rate)]
                        : null;
                    const recentText = channel.recentRates?.length
                      ? channel.recentRates
                          .map((value) => Math.round(value))
                          .join(" → ")
                      : null;
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
                        <span className="ch-state">
                          <ToneTag
                            tone={metricTone ?? (last?.ok ? "green" : "gray")}
                          >
                            {last?.status ?? "unknown"}
                          </ToneTag>
                          {recentText && (
                            <span className="ch-sub mono">
                              近三轮 {recentText}
                            </span>
                          )}
                        </span>
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
                        <span className="mono ch-metrics">
                          {[
                            channel.successRate24h != null
                              ? `24h ${channel.successRate24h.toFixed(2)}%`
                              : null,
                            last?.tps != null
                              ? `${Math.round(last.tps)} tps`
                              : null,
                          ]
                            .filter(Boolean)
                            .join(" · ") || "—"}
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
