import Link from "next/link";
import { apiGet } from "@/lib/api";
import { Btn } from "@/components/ui";
import { eventMeta, formatDiscount, formatTime } from "@/lib/format";
import { getSiteInfo } from "@/lib/sites";
import type { EventListData, HistoryListData, MetaData, OverviewData } from "@/lib/types";
import { RiskLink } from "@/components/RiskLink";
import { ComingSoon } from "@/components/ui";
import { LandingFeatures } from "@/components/LandingFeatures";
import { HeroTrendChart } from "@/components/HeroTrendChart";
import { SnapshotPreview } from "@/components/SnapshotPreview";
import { SiteAlert } from "@/components/SiteAlert";
import { TermTip, type TermKey } from "@/components/TermTip";

export const dynamic = "force-dynamic";

interface LandingData {
  overview: OverviewData | null;
  meta: MetaData | null;
  events: EventListData | null;
  history: HistoryListData | null;
  error: string | null;
}

async function loadLanding(): Promise<LandingData> {
  try {
    const [overview, meta, events] = await Promise.all([
      apiGet<OverviewData>("/api/overview"),
      apiGet<MetaData>("/api/meta"),
      apiGet<EventListData>("/api/events?limit=8"),
    ]);
    // hero 折线是装饰位：历史拉取失败只影响图表兜底回插画，不阻塞整页报错
    let history: HistoryListData | null;
    try {
      history = await apiGet<HistoryListData>("/api/history?limit=1000");
    } catch {
      history = null;
    }
    return { overview, meta, events, history, error: null };
  } catch (cause) {
    return {
      overview: null,
      meta: null,
      events: null,
      history: null,
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
  const { overview, meta, events, history, error } = await loadLanding();
  const records = overview?.records ?? [];
  const sites = collectSites(overview, meta);
  const latestEvents = events?.events.slice(-4).reverse() ?? [];
  const historyRecords = history?.records ?? [];

  const siteIds = new Set(records.map((row) => row.site_id));
  const modelIds = new Set(records.map((row) => row.model));

  // 实时亮点：最低输入折扣（及所属站点）、最近采集、监控覆盖
  const discounted = records
    .filter((row) => row.discount?.input !== null && row.discount?.input !== undefined)
    .sort((a, b) => (a.discount?.input ?? 9) - (b.discount?.input ?? 9));
  const best = discounted[0];
  const latestAt = records.reduce<number | undefined>(
    (acc, row) => (acc === undefined || row.captured_at > acc ? row.captured_at : acc),
    undefined,
  );

  const highlights: {
    tone: "green" | "yellow" | "blue";
    title: string;
    value: string;
    sub: string;
    tip?: TermKey;
    /** 0–1 比例条（可选）：给折扣类指标一个相对官方价的量感 */
    bar?: number;
  }[] = [
    {
      tone: "green" as const,
      title: "最低输入折扣",
      tip: "discount_input",
      value: best?.discount?.input !== null && best?.discount?.input !== undefined ? formatDiscount(best.discount.input) : "—",
      sub: best ? `${getSiteInfo(best.site_id, best.source_url).name} · 相对厂商官方价` : "暂无折扣数据",
      bar: best?.discount?.input ?? undefined,
    },
    {
      tone: "yellow" as const,
      title: "最近采集",
      value: latestAt ? formatTime(latestAt).slice(0, 10) : "—",
      sub: latestAt ? `${formatTime(latestAt).slice(11)} · 全站最新快照` : "等待第一轮采集",
    },
    {
      tone: "blue" as const,
      title: "监控覆盖",
      value: `${siteIds.size} 站 · ${modelIds.size} 模型`,
      sub: `累计 ${records.length} 条价格记录`,
    },
  ];

  return (
    <div className="page landing">
      <section className="hero">
        <div className="hero-main">
          <h1 className="hero-title">
            中转站价格，
            <br />
            条条有出处。
          </h1>
          <p className="hero-sub">
            多站点价格快照、厂商官方价对照、变化事件追踪。所有价格都来自对站点的直接抓取，
            每一条都能点开来源核对，不做任何估算。
          </p>
          <div className="hero-actions">
            <Link href="/overview">
              <Btn variant="primary" size="lg">
                进入价格总览 →
              </Btn>
            </Link>
            <Link href="/discount">
              <Btn size="lg">查看折扣对比</Btn>
            </Link>
          </div>
        </div>
        <aside className="hero-side">
          <HeroTrendChart records={historyRecords} />
          <div className="hero-side-title">最新事件</div>
          {latestEvents.length > 0 ? (
            <>
              {latestEvents.map((event, index) => {
                const meta = eventMeta(event.kind);
                const site = getSiteInfo(event.site_id, event.current?.source_url ?? event.previous?.source_url);
                return (
                  <Link key={`${event.site_id}:${event.model}:${event.detected_at}:${index}`} href="/history" className="side-note">
                    <span className="side-note-line">
                      <span className={`side-dot dot-${meta.tone}`} />
                      <span className="side-note-title">
                        {site.name} · {meta.label}
                      </span>
                    </span>
                    <span className="side-note-sub">
                      <span className="mono">{event.model}</span> · {formatTime(event.detected_at)}
                    </span>
                  </Link>
                );
              })}
              <Link href="/history" className="landing-more">
                全部事件 →
              </Link>
            </>
          ) : (
            <p style={{ color: "var(--text-3)", fontSize: 13, margin: 0 }}>还没有事件，完成第一轮采集后会显示在这里。</p>
          )}
        </aside>
      </section>

      {error && <SiteAlert title="暂时读不到监控数据" detail={error} fix="请稍后刷新重试；若持续出现，欢迎通过页脚「提建议」告诉我们。" />}

      <section className="landing-section">
        <div className="landing-section-head">
          <h2>实时亮点</h2>
        </div>
        <div className="landing-stats">
          {highlights.map((item) => (
            <div key={item.title} className={`landing-stat lstat-${item.tone}`}>
              <div className="stat-label">
                <span aria-hidden className={`stat-dot dot-${item.tone}`} />
                {item.title}
                {item.tip && <TermTip term={item.tip} />}
              </div>
              <div className="hl-value">{item.value}</div>
              {item.bar !== undefined && item.bar >= 0.05 && (
                <span className="disc-bar-track hl-bar" aria-hidden>
                  <span className={`disc-bar-fill tone-${item.tone}`} style={{ width: `${Math.round(Math.min(Math.max(item.bar, 0), 1) * 100)}%` }} />
                </span>
              )}
              <div className="hl-sub">{item.sub}</div>
            </div>
          ))}
        </div>
      </section>

      {records.length > 0 && (
        <section className="landing-section">
          <div className="landing-section-head">
            <h2>最新快照</h2>
            <Link href="/overview" className="landing-more">
              查看全部 →
            </Link>
          </div>
          <SnapshotPreview records={records.slice(0, 6)} />
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
        {sites.length > 0 ? (
          <div className="site-list">
            {sites.map((site) => {
              const info = getSiteInfo(site.id, site.sourceUrl);
              const href = info.homepage || site.sourceUrl || "";
              return (
                <div key={site.id} className="site-row">
                  <span
                    aria-hidden
                    className="site-dot"
                    style={{ background: site.enabled ? "var(--accent)" : "var(--text-3)" }}
                  />
                  <span className="site-name">
                    {href ? (
                      <RiskLink href={href} variant="site">
                        {info.name}
                      </RiskLink>
                    ) : (
                      info.name
                    )}
                  </span>
                  <span className="site-count mono">
                    {site.models} 模型{site.enabled ? "" : " · 已停用"}
                  </span>
                </div>
              );
            })}
          </div>
        ) : (
          <p style={{ color: "var(--text-2)" }}>还没有站点数据。</p>
        )}
      </section>

      <section className="landing-section">
        <div className="landing-section-head">
          <h2>价格从哪里来</h2>
        </div>
        <LandingFeatures />
      </section>
    </div>
  );
}
