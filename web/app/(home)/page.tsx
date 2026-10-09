import Link from "next/link";
import { apiGet, PUBLIC_REVALIDATE } from "@/lib/api";
import { Btn } from "@/components/ui";
import {
  eventMeta,
  formatPrice,
  formatTime,
  formatTimeAgo,
  isNoticeEvent,
  noticeExcerpt,
  toCnyPrice,
} from "@/lib/format";
import { getSiteInfo } from "@/lib/sites";
import { canonicalModel, lowestPriceRowPerModel, orderByReleaseDesc } from "@/lib/priceRows";
import { foldEvents } from "@/lib/events";
import {
  buildSiteViews,
  rateLevel,
  type RateLevel,
} from "@/lib/channelStatus";
import type {
  CatalogData,
  FeedData,
  HistoryListData,
  MetaData,
  OverviewData,
  RankingsData,
  StatusSnapshot,
} from "@/lib/types";
import { SiteSubmitButton } from "@/components/SiteSubmitModal";
import { SectionRail } from "@/components/SectionRail";
import { Reveal } from "@/components/Reveal";
import { HeroType } from "@/components/HeroType";
import { HeroArea } from "@/components/HeroArea";
import type { GlobeSite, SiteGeo } from "@/components/SiteMapFlat";
import { HeroTrendChart } from "@/components/HeroTrendChart";
import { SnapshotPreview } from "@/components/SnapshotPreview";
import { SiteAlert } from "@/components/SiteAlert";
import { DajuChartNap, DajuNap, DajuYarn } from "@/components/DajuArt";
import { FaqList } from "@/components/FaqList";
import { JsonLd } from "@/components/JsonLd";
import { IconAim, IconMonitor, IconSync } from "@/components/icons";
import { alerts, home, site } from "@/lib/copy";
import { siteOrigin } from "@/lib/seo";

/** Bento 小卡图标映射：icon 键 → components/icons.tsx 组件 */
const BENTO_ICONS = {
  sync: IconSync,
  monitor: IconMonitor,
  aim: IconAim,
} as const;

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
  /** 厂商目录：只取发布日期建排序索引，拉取失败只影响最新价格的排序口径 */
  catalog: CatalogData | null;
  error: string | null;
}

/** 站点瓷贴状态色：与详情页图表 statusColors 同一套（亮/暗各一组，见 globals.css --chart-*），
 *  阈值一致：可用率 80/60 三档，延迟 1000/3000ms 三档。 */
const STRIP_TONE: Record<RateLevel, string> = {
  ok: "var(--chart-ok)",
  warn: "var(--chart-warn)",
  down: "var(--chart-down)",
};

/** 瓷贴状态点：正常点降到半强度（大面积重复的好状态不抢注意力），异常/延迟档保持全色。 */
const TILE_DOT_TONE: Record<RateLevel, string> = {
  ok: "color-mix(in srgb, var(--chart-ok) 45%, transparent)",
  warn: "var(--chart-warn)",
  down: "var(--chart-down)",
};

/** 行列表封顶：首页只列这么多行，其余进「还有 N 个站点」链接 */
const SITE_ROW_MAX = 18;

/** 最新事件侧栏展示条数：feed 取 8 条折叠后截到这里，渲染处直接整组上 */
const LATEST_EVENTS_SHOWN = 5;

async function loadLanding(): Promise<LandingData> {
  try {
    // 统一事件流（价格+公告已合并）；渠道检测拉取失败只影响星球与站点卡片，不阻塞整页
    const [overview, meta, feed, status, geo, rankings, catalog, history] = await Promise.all([
      apiGet<OverviewData>("/api/overview", undefined, PUBLIC_REVALIDATE),
      apiGet<MetaData>("/api/meta", undefined, PUBLIC_REVALIDATE),
      apiGet<FeedData>("/api/feed?events_limit=8&notice_limit=8", undefined, PUBLIC_REVALIDATE).catch(() => null),
      // 与站点检测详情页同口径：最近 7 天、每站最近 400 条；再给全量行数上限——
      // 站点多时 400×N 条状态快照体积失控（5 站 7 天实测约 9MB），按 id 均匀抽样到
      // 600 条封顶（每站最新一条始终保留，时段桶均值仍是全量分布的近似）
      // since 对齐到分钟：60s 缓存窗口内 URL 稳定，fetch 缓存才能命中
      apiGet<{ records: StatusSnapshot[] }>(
        `/api/status?per_site=400&since=${Math.floor((Date.now() / 1000 - 7 * 86_400) / 60) * 60}&max_records=600`,
        undefined,
        PUBLIC_REVALIDATE,
      ).catch(() => ({ records: [] as StatusSnapshot[] })),
      // 站点定位较慢（DNS + 归属地查询），失败只影响地球落点，不阻塞整页
      apiGet<{ geo: Record<string, SiteGeo> }>("/api/geo", undefined, PUBLIC_REVALIDATE).catch(() => ({ geo: {} })),
      apiGet<RankingsData>("/api/rankings", undefined, PUBLIC_REVALIDATE).catch(() => null),
      apiGet<CatalogData>("/api/catalog", undefined, PUBLIC_REVALIDATE).catch(() => null),
      // hero 折线是装饰位：拉取失败只影响图表兜底回插画，不阻塞整页报错；与其余取数同走 60s 缓存
      apiGet<HistoryListData>("/api/history?limit=1000", undefined, PUBLIC_REVALIDATE).catch(() => null),
    ]);
    return { overview, meta, feed, history, status: status.records, geo: geo.geo, rankings, catalog, error: null };
  } catch (cause) {
    return {
      overview: null,
      meta: null,
      feed: null,
      history: null,
      status: [],
      geo: {},
      rankings: null,
      catalog: null,
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
  const { overview, meta, feed, history, status, geo, rankings, catalog, error } = await loadLanding();
  // JSON-LD 里的站点地址要绝对 URL，与 metadataBase 同一推导口径
  const origin = await siteOrigin();
  const records = overview?.records ?? [];
  const sites = collectSites(overview, meta);
  // 首页榜单速览：基础模型去重后的 Top 5；数值条按榜首归一
  const rankingsTop = topBaseModels(rankings);
  const maxRankIndex = Math.max(0, ...rankingsTop.map((entry) => entry.intelligence_index ?? 0));
  // 站点价统一按 RMB 展示：汇率取厂商价快照口径
  const rate = overview?.catalog?.usd_cny_rate ?? null;
  // 最新事件侧栏与事件追踪页同一套折叠口径（同站同模型同类 120s 内折一张卡），
  // 原始事件按「站点+模型+分组」入库，不折叠会同模型并排出几条看起来一样的行；公告事件没有模型行，展示公告摘要
  const latestEventGroups = foldEvents(feed?.events ?? []).slice(0, LATEST_EVENTS_SHOWN);
  const historyRecords = history?.records ?? [];

  // 站点检测档案：可用率序列供星球悬停与行列表状态共用；
  // 阈值与详情页一致：可用率 80/60 三档，延迟 1000/3000ms 三档
  const siteViews = buildSiteViews(status);
  const availBySite = siteViews.availability;
  const bucketsBySite = siteViews.uptimeBuckets;
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
  // 有检测档案但解析不出时间线的站点，文案与「未接入」区分开
  const statusSiteIds = new Set(status.map((row) => row.site_id));

  // 行列表（Statuspage 模式）：down 站排最前、warn 次之、无数据其后、停用垫底，封顶取前 SITE_ROW_MAX 行；
  // 平均可用率只算启用且有检测数据的站点，停用站的旧数据不掺进来
  const LEVEL_RANK: Record<RateLevel | "none" | "off", number> = {
    down: 0,
    warn: 1,
    ok: 2,
    none: 3,
    off: 4,
  };
  const rankOf = (site: (typeof sites)[number]) => {
    if (!site.enabled) return LEVEL_RANK.off;
    const latest = (availBySite[site.id] ?? []).slice(-1)[0];
    if (!latest) return LEVEL_RANK.none;
    return LEVEL_RANK[rateLevel(latest.pct)];
  };
  const orderedSites = [...sites].sort((a, b) => rankOf(a) - rankOf(b));
  const wallSites = orderedSites.slice(0, SITE_ROW_MAX);
  /** 站点卡状态色：正常档半强度（大面积绿不抢眼），异常全色；阈值与详情页共用 */
  const levelColor = (level: RateLevel) =>
    level === "ok"
      ? "color-mix(in srgb, var(--chart-ok) 55%, transparent)"
      : level === "warn"
        ? "var(--chart-warn)"
        : "var(--chart-down)";

  // 首页精选：每个模型归一合并后只留综合价最低的一行，按目录发布日期倒序（新模型在前），
  // 与总览页模型下拉同一套口径；目录没收录的模型垫底按名称
  const releaseByModel: Record<string, string> = {};
  for (const entry of Object.values(catalog?.models ?? {})) {
    if (entry?.model) releaseByModel[canonicalModel(entry.model)] = entry.release_date ?? "";
  }
  const parentRows = orderByReleaseDesc(lowestPriceRowPerModel(records, rate), releaseByModel);

  return (
    <>
      {/* 结构化数据：站点身份 + 首页常见问题（内容与页面上 FaqList 同源，满足 FAQPage 可见性要求） */}
      <JsonLd
        data={{
          "@context": "https://schema.org",
          "@graph": [
            {
              "@type": "WebSite",
              name: site.name,
              url: origin,
              description: site.description,
              inLanguage: "zh-CN",
            },
            {
              "@type": "FAQPage",
              mainEntity: home.faq.map((item) => ({
                "@type": "Question",
                name: item.q,
                acceptedAnswer: { "@type": "Answer", text: item.a },
              })),
            },
          ],
        }}
      />
      <div className="page landing">
        <SectionRail />
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

        {/* Bento 产品介绍：大卡 = 主价值 + 真实价格行预览（Apple 式文/视觉分栏），小卡 = 图标芯片 + 能力一句话 */}
        <Reveal>
          <section className="landing-section" id="sec-intro">
            <div className="bento-grid">
              <Link href="/overview" className="bento-card bento-card--feature">
                <div className="bento-copy">
                  <span className="bento-title">{home.introBento.main.title}</span>
                  <p className="bento-desc">{home.introBento.main.desc}</p>
                  <span className="landing-more">{home.introBento.main.cta}</span>
                </div>
                <div className="bento-visual" aria-hidden>
                  <div className="bento-mini-head">
                    <span>站点</span>
                    <span>模型</span>
                    <span>输入价 /M</span>
                  </div>
                  {parentRows.slice(0, 3).map((row) => {
                    const site = getSiteInfo(row.site_id, row.source_url);
                    const converted = toCnyPrice(row.input_price, row.unit, rate);
                    return (
                      <div key={`${row.site_id}:${row.model}`} className="bento-mini-row">
                        <span className="bento-mini-site">{site.name}</span>
                        <span className="mono bento-mini-model">{row.model}</span>
                        <span className="mono bento-mini-price">
                          {converted !== null ? "¥" : ""}
                          {formatPrice(converted ?? row.input_price)}
                        </span>
                      </div>
                    );
                  })}
                  <span className="bento-mini-src mono">llmprices.cn · 每行附来源链接 ↗</span>
                </div>
              </Link>
              {home.introBento.items.map((item) => {
                const Icon = BENTO_ICONS[item.icon];
                return (
                  <div key={item.title} className="bento-card">
                    <span className="bento-icon" aria-hidden>
                      <Icon size={20} />
                    </span>
                    <span className="bento-title">{item.title}</span>
                    <p className="bento-desc">{item.desc}</p>
                  </div>
                );
              })}
            </div>
          </section>
        </Reveal>

        {records.length > 0 && (
          <Reveal>
            <section className="landing-section" id="sec-latest">
              <div className="landing-section-head">
                <div className="landing-section-title">
                  <h2>{home.sections.latestPrice}</h2>
                </div>
                <Link href="/overview" className="landing-more">
                  {home.viewAll}
                </Link>
              </div>
              <p className="landing-section-sub">{home.sectionSubs.latestPrice}</p>
              <SnapshotPreview rows={parentRows.slice(0, 12)} rate={rate} />
            </section>
          </Reveal>
        )}

        <Reveal>
          <section className="landing-section landing-duo" id="sec-trend">
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
              {latestEventGroups.length > 0 ? (
                <div className="landing-events">
                  {latestEventGroups.map((group, index) => {
                    const event = group[0];
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
                        className="event-row"
                      >
                        <span className={`event-badge tone-${meta.tone}`}>
                          {meta.label}
                        </span>
                        <span className="event-main">
                          <span className="event-site">{site.name}</span>
                          <span className="event-sub">
                            {isNoticeEvent(event) ? (
                              <span title={event.content}>
                                {noticeExcerpt(event.content)}
                              </span>
                            ) : (
                              <span className="mono">{event.model}</span>
                            )}
                          </span>
                        </span>
                        <span className="event-time mono num">
                          {formatTime(event.detected_at)}
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
          <section className="landing-section" id="sec-sites">
            <div className="landing-section-head">
              <div className="landing-section-title">
                <h2>{home.sections.sites}</h2>
              </div>
              <SiteSubmitButton label={home.submitSite} variant="text" />
            </div>
            <p className="landing-section-sub">{home.sectionSubs.sites}</p>
            {sites.length > 0 ? (
              <div className="site-grid">
                {wallSites.map((site) => {
                  const info = getSiteInfo(site.id, site.sourceUrl);
                  const statusHref = `/overview/status/${encodeURIComponent(site.id)}`;
                  const latest = (availBySite[site.id] ?? []).slice(-1)[0];
                  const pct = site.enabled && latest ? latest.pct : null;
                  const level = pct != null ? rateLevel(pct) : null;
                  return (
                    <Link
                      key={site.id}
                      href={statusHref}
                      className={`site-card${site.enabled ? "" : " site-card--off"}`}
                      title={`${info.name} · ${pct != null ? `最新正常 ${pct}%` : site.enabled ? home.empty.siteNoCheckRecord : home.empty.siteDisabled}`}
                    >
                      <span className="site-card-head">
                        <span
                          aria-hidden
                          className="site-card-dot"
                          style={{
                            background:
                              level != null
                                ? levelColor(level)
                                : "var(--text-3)",
                          }}
                        />
                        <span className="site-card-name">{info.name}</span>
                        <span
                          className="site-card-avg mono num"
                          style={level ? { color: levelColor(level) } : undefined}
                        >
                          {pct != null ? `${pct}%` : "—"}
                        </span>
                      </span>
                      <span className="site-card-meta">
                        {latest
                          ? `${formatTimeAgo(latest.at)}检测`
                          : site.enabled
                            ? home.empty.siteNoCheckRecord
                            : home.empty.siteDisabled}
                      </span>
                    </Link>
                  );
                })}
                {sites.length > wallSites.length && (
                  <Link href="/overview" className="site-card site-card--more">
                    <span className="site-card-more-label">
                      {home.sitesOverview.more(sites.length - wallSites.length)}
                    </span>
                  </Link>
                )}
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
            <section className="landing-section" id="sec-rankings">
              <div className="landing-section-head">
                <div className="landing-section-title">
                  <h2>{home.sections.rankings}</h2>
                </div>
                <Link href="/rankings" className="landing-more">
                  完整榜单 →
                </Link>
              </div>
              <p className="landing-section-sub">{home.sectionSubs.rankings}</p>
              <div className="rank-list">
                <div className="rank-list-head" aria-hidden>
                  <span />
                  <span>模型</span>
                  <span>厂商</span>
                  <span>智能指数</span>
                </div>
                {rankingsTop.map((entry) => (
                  <Link key={entry.slug} href="/rankings" className="rank-row">
                    <span className="rank-row-no mono num">#{entry.rank}</span>
                    <span className="rank-row-name mono" title={entry.name}>
                      {entry.name.replace(/\s*\([^)]*\)$/, "")}
                    </span>
                    <span className="rank-row-creator">{entry.creator ?? "—"}</span>
                    <span className="rank-row-index mono num">
                      {entry.intelligence_index ?? "—"}
                      {entry.intelligence_index != null && maxRankIndex > 0 && (
                        <span
                          aria-hidden
                          className="rank-row-bar"
                          style={{
                            width: `${Math.round((entry.intelligence_index / maxRankIndex) * 100)}%`,
                          }}
                        />
                      )}
                    </span>
                  </Link>
                ))}
              </div>
            </section>
          </Reveal>
        )}

        {/* 常见问题：全内容宽度铺开 */}
        <Reveal>
          <section className="landing-section" id="sec-faq">
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
