import Link from "next/link";
import { apiGet } from "@/lib/api";
import { Btn } from "@/components/ui";
import {
  currencySymbol,
  eventMeta,
  formatPrice,
  formatTime,
  isNoticeEvent,
  noticeExcerpt,
  toCnyPrice,
} from "@/lib/format";
import { getSiteInfo } from "@/lib/sites";
import { sortSnapshotRows } from "@/lib/priceRows";
import {
  buildSiteViews,
  latencyLevel,
  rateLevel,
  type RateLevel,
} from "@/lib/channelStatus";
import type {
  FeedData,
  FeedEvent,
  HistoryListData,
  MetaData,
  OverviewData,
  StatusSnapshot,
} from "@/lib/types";
import { ComingSoon } from "@/components/ui";
import { Reveal } from "@/components/Reveal";
import { HeroType } from "@/components/HeroType";
import { HeroArea } from "@/components/HeroArea";
import type { GlobeSite } from "@/components/SiteGlobe";
import { HeroTrendChart } from "@/components/HeroTrendChart";
import { SnapshotPreview } from "@/components/SnapshotPreview";
import { SiteAlert } from "@/components/SiteAlert";
import { alerts, home } from "@/lib/copy";

export const dynamic = "force-dynamic";

interface LandingData {
  overview: OverviewData | null;
  meta: MetaData | null;
  feed: FeedData | null;
  history: HistoryListData | null;
  status: StatusSnapshot[];
  error: string | null;
}

/** 渠道检测色点：三档色与详情页图表 statusColors 同一套（亮/暗各一组，见 globals.css --chart-*），
 *  阈值一致：可用率 80/60 三档，延迟 1000/3000ms 三档。 */
const STRIP_TONE: Record<RateLevel, string> = {
  ok: "var(--chart-ok)",
  warn: "var(--chart-warn)",
  down: "var(--chart-down)",
};

async function loadLanding(): Promise<LandingData> {
  try {
    // 统一事件流（价格+公告已合并）；渠道检测拉取失败只影响星球与站点卡片，不阻塞整页
    const [overview, meta, feed, status] = await Promise.all([
      apiGet<OverviewData>("/api/overview"),
      apiGet<MetaData>("/api/meta"),
      apiGet<FeedData>("/api/feed?events_limit=8&notice_limit=8").catch(() => null),
      apiGet<{ records: StatusSnapshot[] }>("/api/status?per_site=30").catch(() => ({
        records: [] as StatusSnapshot[],
      })),
    ]);
    // hero 折线是装饰位：历史拉取失败只影响图表兜底回插画，不阻塞整页报错
    let history: HistoryListData | null;
    try {
      history = await apiGet<HistoryListData>("/api/history?limit=1000");
    } catch {
      history = null;
    }
    return { overview, meta, feed, history, status: status.records, error: null };
  } catch (cause) {
    return {
      overview: null,
      meta: null,
      feed: null,
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
  const { overview, meta, feed, history, status, error } = await loadLanding();
  const records = overview?.records ?? [];
  const sites = collectSites(overview, meta);
  // 站点价统一按 RMB 展示：汇率取厂商价快照口径
  const rate = overview?.catalog?.usd_cny_rate ?? null;
  // 最新事件侧栏直接用统一事件流；公告事件没有模型行，展示公告摘要
  const latestEvents: FeedEvent[] = (feed?.events ?? []).slice(0, 4);
  const historyRecords = history?.records ?? [];

  // 站点检测档案：可用率序列供星球悬停与站点卡片色点共用，延迟序列供站点卡片延迟着色；
  // 阈值与详情页一致：可用率 80/60 三档，延迟 1000/3000ms 三档
  const siteViews = buildSiteViews(status);
  const availBySite = siteViews.availability;
  const latencyBySite = siteViews.latency;
  const globeSites: GlobeSite[] = sites.map((site) => {
    const series = availBySite[site.id] ?? [];
    const latestPoint = series[series.length - 1];
    // 节点百分比用近期多次检测的平均渠道正常比例，比"最新一瞬"更能代表日常可用性
    const availability = series.length
      ? Math.round(series.reduce((sum, point) => sum + point.pct, 0) / series.length)
      : null;
    return {
      id: site.id,
      name: getSiteInfo(site.id, site.sourceUrl).name,
      models: site.models,
      enabled: site.enabled,
      availability,
      down: latestPoint?.down.length ?? 0,
      checks: series.length,
    };
  });
  /** 站点卡片上的最近 15 次渠道检测色点 */
  const stripOf = (siteId: string) => (availBySite[siteId] ?? []).slice(-15);
  /** 站点当前延迟：取最新时刻各渠道里最高的一个（最差口径，与详情页着色共用同一阈值） */
  const latestLatencyOf = (siteId: string): number | null => {
    const series = latencyBySite[siteId] ?? [];
    const last = series[series.length - 1];
    if (!last) return null;
    const values = Object.values(last.values);
    return values.length > 0 ? Math.max(...values) : null;
  };
  // 有检测档案但解析不出时间线的站点，文案与「未接入」区分开
  const statusSiteIds = new Set(status.map((row) => row.site_id));

  // 最新快照与价格总览同一口径：不折叠，各分组各占一行
  const parentRows = sortSnapshotRows(records);

  // 终端演示窗内容用真实数据渲染：没有快照时整个窗不出现，不放占位假数
  const termPriceLines = parentRows.slice(0, 3).map((row) => {
    const converted = toCnyPrice(row.input_price, row.unit, rate);
    const price = converted ?? row.input_price;
    const symbol = converted !== null || currencySymbol(row.unit) === "¥" ? "¥" : currencySymbol(row.unit);
    return `site: ${row.site_id}  model: ${row.model}  ${symbol} ${formatPrice(price)} /1M`;
  });
  const firstNotice = Object.values(overview?.notices ?? {})[0];
  const termNoticeLine =
    firstNotice?.content
      ? `「${noticeExcerpt(firstNotice.content, 1).slice(0, 40)}」${
          firstNotice.captured_at ? ` · ${formatTime(firstNotice.captured_at)} 存档` : ""
        }`
      : null;
  const uptimeSample = sites.map((site) => stripOf(site.id)).find((list) => list.length > 0);
  const termUptimeLine = uptimeSample
    ? `${uptimeSample
        .slice(-5)
        .map((point) => (point.pct >= 95 ? "✓" : "✗"))
        .join(" ")}  近 ${uptimeSample.length} 次渠道检测 · 最新正常 ${
        uptimeSample[uptimeSample.length - 1].pct
      }%`
    : null;

  return (
    <>
      <div className="page landing">
        <HeroArea
          sites={globeSites}
          main={
            <>
              <h1 className="hero-title">
                <HeroType />
              </h1>
              <p className="hero-sub">{home.heroSub}</p>
              <div className="hero-actions">
                <Link href="/overview">
                  <Btn variant="primary" size="lg">
                    {home.heroButtons.primary}
                  </Btn>
                </Link>
                <Link href="/discount">
                  <Btn size="lg">{home.heroButtons.secondary}</Btn>
                </Link>
              </div>
            </>
          }
        />

        <Reveal>
          <section className="landing-intro">
            <p>{home.intro}</p>
          </section>
        </Reveal>

        {records.length > 0 && (
          <Reveal>
            <section className="landing-section">
              <div className="landing-section-head">
                <div className="landing-section-title">
                  <span className="section-num">01</span>
                  <h2>{home.sections.latestPrice}</h2>
                </div>
                <Link href="/overview" className="landing-more">
                  {home.viewAll}
                </Link>
              </div>
              <p className="landing-section-sub">{home.sectionSubs.latestPrice}</p>
              <SnapshotPreview rows={parentRows.slice(0, 6)} rate={rate} />
            </section>
          </Reveal>
        )}

        <Reveal>
          <section className="landing-section landing-duo">
            <div>
              <div className="landing-section-head">
                <div className="landing-section-title">
                  <span className="section-num">02</span>
                  <h2>{home.sections.trend}</h2>
                </div>
              </div>
              <HeroTrendChart records={historyRecords} rate={rate} activeModels={[...new Set(records.map((r) => r.model))]} />
            </div>
            <div>
              <div className="landing-section-head">
                <h2>{home.sections.events}</h2>
                <Link href="/history" className="landing-more">
                  {home.viewAllEvents}
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
                          event.current?.source_url ??
                            event.previous?.source_url,
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
                  {home.empty.events}
                </p>
              )}
            </div>
          </section>
        </Reveal>

        {error && (
          <SiteAlert title={alerts.loadData.title} detail={error} fix={alerts.loadData.fix} />
        )}

        <Reveal>
          <section className="landing-section">
            <div className="landing-section-head">
              <div className="landing-section-title">
                <span className="section-num">03</span>
                <h2>{home.sections.sites}</h2>
              </div>
              <ComingSoon
                label={home.submitSite}
                variant="text"
                title={home.submitSite}
                description={home.submitSiteDesc}
              />
            </div>
            <p className="landing-section-sub">{home.sectionSubs.sites}</p>
            {sites.length > 0 ? (
              <div className="site-cards">
                {sites.map((site) => {
                  const info = getSiteInfo(site.id, site.sourceUrl);
                  const href = info.homepage || site.sourceUrl || "";
                  const strip = stripOf(site.id);
                  const latestPoint = strip[strip.length - 1];
                  const latestLatency = latestLatencyOf(site.id);
                  const notice = overview?.notices?.[site.id];
                  return (
                    <div key={site.id} className="site-card">
                      <div className="site-card-head">
                        <span
                          aria-hidden
                          className="site-dot"
                          style={{
                            background: site.enabled
                              ? "var(--accent)"
                              : "var(--text-3)",
                          }}
                        />
                        <span className="site-name">
                          <Link
                            href={`/overview/status/${encodeURIComponent(site.id)}`}
                            className="site-link"
                          >
                            {info.name}
                          </Link>
                        </span>
                        <span className="site-count mono">
                          {site.models} 模型{site.enabled ? "" : ` · ${home.empty.siteDisabled}`}
                        </span>
                      </div>
                      {strip.length > 0 && latestPoint ? (
                        <>
                          <div className="site-strip" aria-hidden>
                            {strip.map((point, index) => (
                              <span
                                key={index}
                                className="site-strip-dot"
                                style={{
                                  background: STRIP_TONE[rateLevel(point.pct)],
                                }}
                                title={`${formatTime(point.at)} · 正常 ${point.pct}%`}
                              />
                            ))}
                          </div>
                          <span className="site-card-more">
                            近 {strip.length} {home.siteCard.checkSuffix}{" "}
                            {latestPoint.pct}%
                            {latestLatency != null && (
                              <>
                                {" "}
                                · {home.siteCard.latencySuffix}{" "}
                                <span
                                  className="mono"
                                  style={{
                                    color: STRIP_TONE[latencyLevel(latestLatency)],
                                  }}
                                  title="延迟 ≥3000ms 红、≥1000ms 黄、其余绿，与站点检测详情页一致"
                                >
                                  {latestLatency} ms
                                </span>
                              </>
                            )}
                          </span>
                        </>
                      ) : (
                        <span className="site-card-nostatus">
                          {statusSiteIds.has(site.id)
                            ? home.empty.siteNoCheckRecord
                            : home.empty.siteCheckNotEnabled}
                        </span>
                      )}
                      <p
                        className="site-notice"
                        title={notice?.content ?? undefined}
                      >
                        {notice ? (
                          <>
                            {notice.captured_at ? (
                              <span className="mono site-notice-time">
                                {formatTime(notice.captured_at)}
                              </span>
                            ) : null}
                            {noticeExcerpt(notice.content)}
                          </>
                        ) : (
                          <span style={{ color: "var(--text-3)" }}>
                            {home.empty.siteNotice}
                          </span>
                        )}
                      </p>
                    </div>
                  );
                })}
              </div>
            ) : (
              <p style={{ color: "var(--text-2)" }}>{home.empty.sites}</p>
            )}
          </section>
        </Reveal>

        <Reveal>
          <section className="landing-section">
            <div className="landing-section-head">
              <div className="landing-section-title">
                <span className="section-num">04</span>
                <h2>{home.sections.dataSource}</h2>
              </div>
            </div>
            <div className="landing-truth">
              <ul className="landing-points">
                {home.dataPoints.map((point) => (
                  <li key={point}>{point}</li>
                ))}
              </ul>
              {termPriceLines.length > 0 && (
                <div className="term" aria-hidden>
                  <div className="term-bar">
                    <span />
                    <span />
                    <span />
                    <span className="term-title">monitor.sh</span>
                  </div>
                  <pre className="term-body">
                    {`$ curl -s /api/overview | head -3
${termPriceLines.join("\n")}${termNoticeLine ? `\n\n$ cat notices.log | tail -1\n${termNoticeLine}` : ""}${
                      termUptimeLine ? `\n\n$ tail -5 uptime.log\n${termUptimeLine}` : ""
                    }`}
                    <span className="term-cursor" />
                  </pre>
                </div>
              )}
            </div>
          </section>
        </Reveal>

        <Reveal>
          <section className="landing-section">
            <div className="landing-section-head">
              <div className="landing-section-title">
                <span className="section-num">05</span>
                <h2>{home.sections.faq}</h2>
              </div>
            </div>
            <div className="faq-list">
              {home.faq.map((item) => (
                <details className="faq-item" key={item.q}>
                  <summary>{item.q}</summary>
                  <p>{item.a}</p>
                </details>
              ))}
            </div>
          </section>
        </Reveal>

        <Reveal>
          <section className="landing-cta">
            <h2>{home.ctaTitle}</h2>
            <Link href="/overview">
              <Btn variant="primary" size="lg">
                进入中转站定价 →
              </Btn>
            </Link>
          </section>
        </Reveal>
      </div>
    </>
  );
}
