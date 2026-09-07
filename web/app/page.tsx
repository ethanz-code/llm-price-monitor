import Link from "next/link";
import { apiGet } from "@/lib/api";
import { Btn } from "@/components/ui";
import {
  eventMeta,
  formatTime,
  isNoticeEvent,
  noticeExcerpt,
} from "@/lib/format";
import { getSiteInfo } from "@/lib/sites";
import { mergeModelRows } from "@/lib/priceRows";
import { availabilityBySite, rateLevel, type RateLevel } from "@/lib/channelStatus";
import type {
  EventListData,
  EventRow,
  HistoryListData,
  MetaData,
  NoticeEvent,
  NoticeEventListData,
  OverviewData,
  StatusLatest,
  StatusSnapshot,
} from "@/lib/types";
import { ComingSoon } from "@/components/ui";
import { GlobePanel } from "@/components/GlobePanel";
import type { GlobeSite } from "@/components/SiteGlobe";
import { HeroTrendChart } from "@/components/HeroTrendChart";
import { SnapshotPreview } from "@/components/SnapshotPreview";
import { SiteAlert } from "@/components/SiteAlert";
import { HeroBackdrop } from "@/components/HeroBackdrop";

export const dynamic = "force-dynamic";

interface LandingData {
  overview: OverviewData | null;
  meta: MetaData | null;
  events: EventListData | null;
  noticeEvents: NoticeEventListData | null;
  history: HistoryListData | null;
  status: StatusSnapshot[];
  error: string | null;
}

/** 渠道检测色点：三档语义色，与可用率趋势图的分段着色一致。 */
const STRIP_TONE: Record<RateLevel, string> = {
  ok: "var(--tone-green-text)",
  warn: "var(--tone-yellow-text)",
  down: "var(--tone-red-text)",
};

async function loadLanding(): Promise<LandingData> {
  try {
    const [overview, meta, events, noticeEvents, status, statusLatest] = await Promise.all([
      apiGet<OverviewData>("/api/overview"),
      apiGet<MetaData>("/api/meta"),
      apiGet<EventListData>("/api/events?limit=8"),
      // 公告事件拉取失败只影响侧栏条目，不阻塞整页
      apiGet<NoticeEventListData>("/api/notice/events?limit=8").catch(() => ({
        events: [],
        total: 0,
      })),
      // 渠道检测档案拉取失败只影响星球与站点卡片，不阻塞整页
      apiGet<{ records: StatusSnapshot[] }>("/api/status?limit=200").catch(() => ({
        records: [] as StatusSnapshot[],
      })),
      // 列表接口有单站条数上限，低频站点会被挤出；latest 兜底每站最新一条
      apiGet<StatusLatest>("/api/status/latest").catch(() => ({} as StatusLatest)),
    ]);
    // hero 折线是装饰位：历史拉取失败只影响图表兜底回插画，不阻塞整页报错
    let history: HistoryListData | null;
    try {
      history = await apiGet<HistoryListData>("/api/history?limit=1000");
    } catch {
      history = null;
    }
    // 两路按站点+时间戳去重合并，覆盖所有已接入检测的站点
    const byStamp = new Map<string, StatusSnapshot>();
    for (const row of status.records) byStamp.set(`${row.site_id}:${row.captured_at}`, row);
    for (const row of Object.values(statusLatest)) byStamp.set(`${row.site_id}:${row.captured_at}`, row);
    return { overview, meta, events, noticeEvents, history, status: [...byStamp.values()], error: null };
  } catch (cause) {
    return {
      overview: null,
      meta: null,
      events: null,
      noticeEvents: null,
      history: null,
      status: [],
      error: cause instanceof Error ? cause.message : String(cause),
    };
  }
}

/** 站点去重：优先 /api/meta 全量清单，退化到快照里出现过的站点。 */
function collectSites(overview: OverviewData | null, meta: MetaData | null) {
  if (meta && meta.sites.length > 0) {
    return meta.sites.map((site) => ({
      id: site.id,
      models: site.models.length,
      enabled: site.enabled,
      sourceUrl: site.url,
    }));
  }
  if (!overview) return [];
  const seen = new Map<string, number>();
  for (const row of overview.records) {
    seen.set(row.site_id, (seen.get(row.site_id) ?? 0) + 1);
  }
  return [...seen.entries()].map(([id, models]) => ({
    id,
    models,
    enabled: true,
    sourceUrl: overview.records.find((row) => row.site_id === id)?.source_url,
  }));
}

export default async function LandingPage() {
  const { overview, meta, events, noticeEvents, history, status, error } =
    await loadLanding();
  const records = overview?.records ?? [];
  const sites = collectSites(overview, meta);
  // 站点价统一按 RMB 展示：汇率取厂商价快照口径
  const rate = overview?.catalog?.usd_cny_rate ?? null;
  // 价格事件与公告事件合并成最新事件侧栏；公告事件没有模型行，展示公告摘要
  const latestEvents: (EventRow | NoticeEvent)[] = [
    ...(events?.events ?? []),
    ...(noticeEvents?.events ?? []),
  ]
    .sort((a, b) => b.detected_at - a.detected_at)
    .slice(0, 4);
  const historyRecords = history?.records ?? [];

  // 站点检测档案：可用率序列供星球悬停与站点卡片色点共用
  const availBySite = availabilityBySite(status);
  const globeSites: GlobeSite[] = sites.map((site) => {
    const series = availBySite[site.id] ?? [];
    const latestPoint = series[series.length - 1];
    return {
      id: site.id,
      name: getSiteInfo(site.id, site.sourceUrl).name,
      models: site.models,
      enabled: site.enabled,
      availability: latestPoint?.pct ?? null,
      down: latestPoint?.down.length ?? 0,
      checks: series.length,
    };
  });
  /** 站点卡片上的最近 15 次渠道检测色点 */
  const stripOf = (siteId: string) => (availBySite[siteId] ?? []).slice(-15);
  // 有检测档案但解析不出时间线的站点，文案与「未接入」区分开
  const statusSiteIds = new Set(status.map((row) => row.site_id));

  // 最新快照与价格总览同一套合并口径：同站点同模型取最低价一行
  const { parentRows, childRowsOf } = mergeModelRows(records);

  return (
    <>
      <HeroBackdrop />
      <div className="page landing">
        <section className="hero">
          <div className="hero-main">
            <h1 className="hero-title">
              中转站
              <br />
              集成式检测平台
            </h1>
            <p className="hero-sub">
              自己用的中转站，是不是时不时就不能用？想找个靠谱的，先来对照各家价格、公告和渠道状态。不推荐任何站点。
            </p>
            <div className="hero-actions">
              <Link href="/overview">
                <Btn variant="primary" size="lg">
                  进入中转站定价 →
                </Btn>
              </Link>
              <Link href="/discount">
                <Btn size="lg">查看折扣对比</Btn>
              </Link>
            </div>
          </div>
          <aside className="hero-globe">
            <GlobePanel sites={globeSites} />
          </aside>
        </section>

        <section className="landing-section landing-duo">
          <div>
            <div className="landing-section-head">
              <h2>价格走势</h2>
            </div>
            <HeroTrendChart records={historyRecords} rate={rate} />
          </div>
          <div>
            <div className="landing-section-head">
              <h2>最新事件</h2>
              <Link href="/history" className="landing-more">
                全部事件 →
              </Link>
            </div>
            {latestEvents.length > 0 ? (
              <div className="landing-events">
                {latestEvents.map((event, index) => {
                  const meta = eventMeta(event.kind);
                  const site = isNoticeEvent(event)
                    ? getSiteInfo(event.site_id)
                    : getSiteInfo(
                        event.site_id,
                        event.current?.source_url ?? event.previous?.source_url,
                      );
                  return (
                    <Link
                      key={`${event.site_id}:${event.kind}:${event.detected_at}:${index}`}
                      href="/history"
                      className="side-note"
                    >
                      <span className="side-note-line">
                        <span className={`side-dot dot-${meta.tone}`} />
                        <span className="side-note-title">
                          {site.name} · {meta.label}
                        </span>
                      </span>
                      <span className="side-note-sub">
                        {isNoticeEvent(event) ? (
                          <span title={event.content}>
                            {noticeExcerpt(event.content)}
                          </span>
                        ) : (
                          <span className="mono">{event.model}</span>
                        )}{" "}
                        · {formatTime(event.detected_at)}
                      </span>
                    </Link>
                  );
                })}
              </div>
            ) : (
              <p style={{ color: "var(--text-3)", fontSize: 13, margin: 0 }}>
                还没有事件记录。
              </p>
            )}
          </div>
        </section>

        {error && (
          <SiteAlert
            title="暂时读不到监控数据"
            detail={error}
            fix="请稍后刷新重试；若持续出现，欢迎通过页脚「提建议」告诉我们。"
          />
        )}

        {records.length > 0 && (
          <section className="landing-section">
            <div className="landing-section-head">
              <h2>最新价格</h2>
              <Link href="/overview" className="landing-more">
                查看全部 →
              </Link>
            </div>
            <p className="landing-section-sub">站点标多少记多少，每条价格都附来源链接，点开就能核对</p>
            <SnapshotPreview
              rows={parentRows.slice(0, 6)}
              childRowsOf={childRowsOf}
              rate={rate}
            />
          </section>
        )}

        <section className="landing-section">
          <div className="landing-section-head">
            <h2>监控中的站点</h2>
            <ComingSoon
              label="提交监控站点"
              variant="text"
              title="提交监控站点"
              description="站点提报功能即将上线，届时填写站点地址即可申请加入监控清单，我们会逐个核验后接入。"
            />
          </div>
          <p className="landing-section-sub">每个站点的渠道检测与公告都自动存档，点站点名进检测档案</p>
          {sites.length > 0 ? (
            <div className="site-cards">
              {sites.map((site) => {
                const info = getSiteInfo(site.id, site.sourceUrl);
                const href = info.homepage || site.sourceUrl || "";
                const strip = stripOf(site.id);
                const latestPoint = strip[strip.length - 1];
                const notice = overview?.notices?.[site.id];
                return (
                  <div key={site.id} className="site-card">
                    <div className="site-card-head">
                      <span
                        aria-hidden
                        className="site-dot"
                        style={{ background: site.enabled ? "var(--accent)" : "var(--text-3)" }}
                      />
                      <span className="site-name">
                        <Link href={`/overview/status/${encodeURIComponent(site.id)}`} className="site-link">
                          {info.name}
                        </Link>
                      </span>
                      <span className="site-count mono">
                        {site.models} 模型{site.enabled ? "" : " · 已停用"}
                      </span>
                    </div>
                    {strip.length > 0 && latestPoint ? (
                      <>
                        <div className="site-strip" aria-hidden>
                          {strip.map((point, index) => (
                            <span
                              key={index}
                              className="site-strip-dot"
                              style={{ background: STRIP_TONE[rateLevel(point.pct)] }}
                              title={`${formatTime(point.at)} · 正常 ${point.pct}%`}
                            />
                          ))}
                        </div>
                        <span className="site-card-more">
                          近 {strip.length} 次渠道检测 · 最新正常 {latestPoint.pct}%
                        </span>
                      </>
                    ) : (
                      <span className="site-card-nostatus">
                        {statusSiteIds.has(site.id) ? "暂无渠道检测记录" : "渠道检测未接入"}
                      </span>
                    )}
                    <p className="site-notice" title={notice?.content ?? undefined}>
                      {notice ? (
                        <>
                          {notice.captured_at ? <span className="mono site-notice-time">{formatTime(notice.captured_at)}</span> : null}
                          {noticeExcerpt(notice.content)}
                        </>
                      ) : (
                        <span style={{ color: "var(--text-3)" }}>暂无公告</span>
                      )}
                    </p>
                  </div>
                );
              })}
            </div>
          ) : (
            <p style={{ color: "var(--text-2)" }}>还没有站点数据。</p>
          )}
        </section>
      </div>
    </>
  );
}
