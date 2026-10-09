import Link from "next/link";
import { apiGet, PUBLIC_REVALIDATE } from "@/lib/api";
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
import { lowestPriceRowPerModel, sortSnapshotRows } from "@/lib/priceRows";
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
  RankingsData,
  StatusSnapshot,
} from "@/lib/types";
import { SiteSubmitButton } from "@/components/SiteSubmitModal";
import { Reveal } from "@/components/Reveal";
import { HeroType } from "@/components/HeroType";
import { HeroArea } from "@/components/HeroArea";
import type { GlobeSite, SiteGeo } from "@/components/SiteGlobe";
import { HeroTrendChart } from "@/components/HeroTrendChart";
import { SnapshotPreview } from "@/components/SnapshotPreview";
import { SiteAlert } from "@/components/SiteAlert";
import { DajuChartNap, DajuNap, DajuYarn } from "@/components/DajuArt";
import { FaqList } from "@/components/FaqList";
import { alerts, home } from "@/lib/copy";

export const dynamic = "force-dynamic";

interface LandingData {
  overview: OverviewData | null;
  meta: MetaData | null;
  feed: FeedData | null;
  history: HistoryListData | null;
  status: StatusSnapshot[];
  /** 站点 IP 归属地：服务端取好传给首屏地球，读接口封锁后浏览器不再直接调 */
  geo: Record<string, SiteGeo>;
  /** AA 榜单：首页速览用，拉取失败只影响榜单小节 */
  rankings: RankingsData | null;
  error: string | null;
}

/** 渠道检测色点：三档色与详情页图表 statusColors 同一套（亮/暗各一组，见 globals.css --chart-*），
 *  阈值一致：可用率 80/60 三档，延迟 1000/3000ms 三档。 */
const STRIP_TONE: Record<RateLevel, string> = {
  ok: "var(--chart-ok)",
  warn: "var(--chart-warn)",
  down: "var(--chart-down)",
};

/** 站点卡点排用：正常点降到半强度（大面积重复的好状态不抢注意力），异常/延迟档保持全色。 */
const STRIP_DOT_TONE: Record<RateLevel, string> = {
  ok: "color-mix(in srgb, var(--chart-ok) 45%, transparent)",
  warn: "var(--chart-warn)",
  down: "var(--chart-down)",
};

async function loadLanding(): Promise<LandingData> {
  try {
    // 统一事件流（价格+公告已合并）；渠道检测拉取失败只影响星球与站点卡片，不阻塞整页
    const [overview, meta, feed, status, geo, rankings] = await Promise.all([
      apiGet<OverviewData>("/api/overview", undefined, PUBLIC_REVALIDATE),
      apiGet<MetaData>("/api/meta", undefined, PUBLIC_REVALIDATE),
      apiGet<FeedData>("/api/feed?events_limit=8&notice_limit=8", undefined, PUBLIC_REVALIDATE).catch(() => null),
      // 与站点检测详情页同口径：最近 7 天、每站最近 400 条（截断不抽样），
      // 星球/轮播要取「最新时段区块平均率」，必须和详情页拿到同一段序列。
      // since 对齐到分钟：60s 缓存窗口内 URL 稳定，fetch 缓存才能命中
      apiGet<{ records: StatusSnapshot[] }>(
        `/api/status?per_site=400&since=${Math.floor((Date.now() / 1000 - 7 * 86_400) / 60) * 60}`,
        undefined,
        PUBLIC_REVALIDATE,
      ).catch(() => ({ records: [] as StatusSnapshot[] })),
      // 站点定位较慢（DNS + 归属地查询），失败只影响地球落点，不阻塞整页
      apiGet<{ geo: Record<string, SiteGeo> }>("/api/geo", undefined, PUBLIC_REVALIDATE).catch(() => ({ geo: {} })),
      apiGet<RankingsData>("/api/rankings", undefined, PUBLIC_REVALIDATE).catch(() => null),
    ]);
    // hero 折线是装饰位：历史拉取失败只影响图表兜底回插画，不阻塞整页报错
    let history: HistoryListData | null;
    try {
      history = await apiGet<HistoryListData>("/api/history?limit=1000");
    } catch {
      history = null;
    }
    return { overview, meta, feed, history, status: status.records, geo: geo.geo, rankings, error: null };
  } catch (cause) {
    return {
      overview: null,
      meta: null,
      feed: null,
      history: null,
      status: [],
      geo: {},
      rankings: null,
      error: cause instanceof Error ? cause.message : String(cause),
    };
  }
}

/** 榜单去重：推理档位变体（-xhigh/-high/-medium/-low 后缀）只留排名最靠前的基础模型行。 */
function topBaseModels(rankings: RankingsData | null, count = 5) {
  if (!rankings) return [];
  const seen = new Set<string>();
  const top: RankingsData["models"] = [];
  for (const entry of rankings.models) {
    const base = entry.slug.replace(/-(xhigh|high|medium|low)$/, "");
    if (seen.has(base)) continue;
    seen.add(base);
    top.push(entry);
    if (top.length >= count) break;
  }
  return top;
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
  const { overview, meta, feed, history, status, geo, rankings, error } = await loadLanding();
  const records = overview?.records ?? [];
  const sites = collectSites(overview, meta);
  // 首页榜单速览：基础模型去重后的 Top 5
  const rankingsTop = topBaseModels(rankings);
  // 站点价统一按 RMB 展示：汇率取厂商价快照口径
  const rate = overview?.catalog?.usd_cny_rate ?? null;
  // 最新事件侧栏直接用统一事件流；公告事件没有模型行，展示公告摘要
  const latestEvents: FeedEvent[] = (feed?.events ?? []).slice(0, 4);
  const historyRecords = history?.records ?? [];

  // 站点检测档案：可用率序列供星球悬停与站点卡片色点共用，延迟序列供站点卡片延迟着色；
  // 阈值与详情页一致：可用率 80/60 三档，延迟 1000/3000ms 三档
  const siteViews = buildSiteViews(status);
  const availBySite = siteViews.availability;
  const bucketsBySite = siteViews.uptimeBuckets;
  const latencyBySite = siteViews.latency;
  const globeSites: GlobeSite[] = sites.map((site) => {
    const series = availBySite[site.id] ?? [];
    const latestPoint = series[series.length - 1];
    // 节点百分比与详情页顶部时段色块同口径：最新一个时段区块的平均正常率
    // （uptimeBuckets 由服务端用抽稀前的全量序列分桶，均值不受「保峰抽稀只留最差」影响）
    const siteBuckets = bucketsBySite[site.id] ?? [];
    const latestBucket = siteBuckets[siteBuckets.length - 1];
    const availability = latestBucket ? latestBucket.avg : null;
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

  // 首页精选：每个模型归一合并后只留综合价最低的一行，再按采集时间倒序
  const parentRows = sortSnapshotRows(lowestPriceRowPerModel(records, rate));

  // 终端演示窗内容用真实数据渲染：没有快照时整个窗不出现，不放占位假数。
  // 命令行首用虚构的 monitor.sh / 日志文件名，不出现本站任何真实接口路径
  const termPriceLines = parentRows.slice(0, 3).map((row) => {
    const converted = toCnyPrice(row.input_price, row.unit, rate);
    const price = converted ?? row.input_price;
    const symbol = converted !== null || currencySymbol(row.unit) === "¥" ? "¥" : currencySymbol(row.unit);
    return `site: ${getSiteInfo(row.site_id).name || row.site_id}  model: ${row.model}  ${symbol} ${formatPrice(price)} /1M`;
  });
  const firstNotice = Object.values(overview?.notices ?? {})[0];
  const termNoticeLine = firstNotice?.content
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
          geo={geo}
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
                <Link href="/calculator">
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
                <div className="landing-empty-compact">
                  <DajuYarn width={72} />
                  <span>{home.empty.events}</span>
                </div>
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
                <h2>{home.sections.sites}</h2>
              </div>
              <SiteSubmitButton label={home.submitSite} variant="text" />
            </div>
            <p className="landing-section-sub">{home.sectionSubs.sites}</p>
            {sites.length > 0 ? (
              <div className="site-cards">
                {sites.map((site) => {
                  const info = getSiteInfo(site.id, site.sourceUrl);
                  const statusHref = `/overview/status/${encodeURIComponent(site.id)}`;
                  const strip = stripOf(site.id);
                  const latestPoint = strip[strip.length - 1];
                  const latestLatency = latestLatencyOf(site.id);
                  const notice = overview?.notices?.[site.id];
                  return (
                    <Link key={site.id} href={statusHref} className="site-card">
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
                        <span className="site-name">{info.name}</span>
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
                                  background: STRIP_DOT_TONE[rateLevel(point.pct)],
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
                    </Link>
                  );
                })}
              </div>
            ) : (
              <div className="landing-empty">
                <DajuNap width={150} />
                <p>{home.empty.sites}</p>
              </div>
            )}
          </section>
        </Reveal>

        {rankingsTop.length > 0 && (
          <Reveal>
            <section className="landing-section">
              <div className="landing-section-head">
                <div className="landing-section-title">
                  <h2>{home.sections.rankings}</h2>
                </div>
                <Link href="/rankings" className="landing-more">
                  完整榜单 →
                </Link>
              </div>
              <p className="landing-section-sub">{home.sectionSubs.rankings}</p>
              <div className="rank-cards">
                {rankingsTop.map((entry) => (
                  <Link key={entry.slug} href="/rankings" className="rank-card">
                    <span className="rank-card-head">
                      <span className="mono">#{entry.rank}</span>
                      <span>{entry.creator ?? "—"}</span>
                    </span>
                    <span className="rank-card-name mono" title={entry.name}>
                      {entry.name.replace(/\s*\([^)]*\)$/, "")}
                    </span>
                    <span className="rank-card-index mono">
                      {entry.intelligence_index ?? "—"}
                    </span>
                    <span className="rank-card-label">智能指数</span>
                  </Link>
                ))}
              </div>
            </section>
          </Reveal>
        )}

        <Reveal>
          <section className="landing-section">
            <div className="landing-section-head">
              <div className="landing-section-title">
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
                <div className="term">
                  <div className="term-bar">
                    <span />
                    <span />
                    <span />
                    <span className="term-title">monitor.sh</span>
                  </div>
                  <pre className="term-body">
                    {`$ ./monitor.sh
${termPriceLines.join("\n")}${
                      termNoticeLine ? `\n\n$ cat notices.log | tail -1\n${termNoticeLine}` : ""
                    }${termUptimeLine ? `\n\n$ tail -5 uptime.log\n${termUptimeLine}` : ""}`}
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
                <h2>{home.sections.faq}</h2>
              </div>
            </div>
            <FaqList items={home.faq} />
          </section>
        </Reveal>

        <Reveal>
          <section className="landing-cta">
            <div className="landing-cta-art">
              <DajuChartNap width={300} />
            </div>
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
