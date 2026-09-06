import Link from "next/link";
import { apiGet } from "@/lib/api";
import { Btn } from "@/components/ui";
import { eventMeta, formatDiscount, formatTime } from "@/lib/format";
import { getSiteInfo } from "@/lib/sites";
import type { EventListData, MetaData, OverviewData } from "@/lib/types";
import { RiskLink } from "@/components/RiskLink";
import { ComingSoon } from "@/components/ui";
import { LandingFeatures } from "@/components/LandingFeatures";
import { Reveal } from "@/components/Reveal";
import { SnapshotPreview } from "@/components/SnapshotPreview";
import { SiteAlert } from "@/components/SiteAlert";

export const dynamic = "force-dynamic";

interface LandingData {
  overview: OverviewData | null;
  meta: MetaData | null;
  events: EventListData | null;
  error: string | null;
}

async function loadLanding(): Promise<LandingData> {
  try {
    const [overview, meta, events] = await Promise.all([
      apiGet<OverviewData>("/api/overview"),
      apiGet<MetaData>("/api/meta"),
      apiGet<EventListData>("/api/events?limit=8"),
    ]);
    return { overview, meta, events, error: null };
  } catch (cause) {
    return {
      overview: null,
      meta: null,
      events: null,
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

/** Highlights 卡片侧色（AA 的彩色方块语法，取自站内语义色板）。 */
const TONE_SQUARES = { green: "#c8ff00", yellow: "#e0b45c", blue: "#5b9bff" } as const;

/** 事件色点与 Highlights 方块同源。 */
function eventDotColor(tone: string): string {
  if (tone === "green") return "#7cc47f";
  if (tone === "red") return "#e27b78";
  if (tone === "yellow") return "#e0b45c";
  if (tone === "blue") return "#5b9bff";
  return "#9aa0a8";
}

export default async function LandingPage() {
  const { overview, meta, events, error } = await loadLanding();
  const records = overview?.records ?? [];
  const sites = collectSites(overview, meta);
  const latestEvents = events?.events.slice(-4).reverse() ?? [];

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

  const highlights = [
    {
      tone: "green" as const,
      title: "最低输入折扣",
      value: best?.discount?.input !== null && best?.discount?.input !== undefined ? formatDiscount(best.discount.input) : "—",
      sub: best ? `${getSiteInfo(best.site_id, best.source_url).name} · 相对厂商官方价` : "暂无折扣数据",
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
            逐条取证。
          </h1>
          <p className="hero-sub">
            多站点价格快照、厂商官方价锚定、变化事件追踪。所有价格事实均来自直接请求的
            HTTP 响应，不做任何猜测。
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
          <div className="hero-side-title">最新事件</div>
          {latestEvents.length > 0 ? (
            <>
              {latestEvents.map((event, index) => {
                const meta = eventMeta(event.kind);
                const site = getSiteInfo(event.site_id, event.current?.source_url ?? event.previous?.source_url);
                return (
                  <Link key={`${event.site_id}:${event.model}:${event.detected_at}:${index}`} href="/history" className="side-note">
                    <span className="side-note-line">
                      <span className="side-dot" style={{ background: eventDotColor(meta.tone) }} />
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

      {error && <SiteAlert title="无法读取监控数据" detail={error} fix="请确认后端已启动：uv run price-web" />}

      <Reveal>
        <section className="landing-section">
          <div className="landing-section-head">
            <h2>
              <span className="hl-square" style={{ background: TONE_SQUARES.green }} />
              实时亮点
            </h2>
          </div>
          <div className="highlight-grid">
            {highlights.map((item) => (
              <div key={item.title} className="highlight-card">
                <div className="hl-head">
                  <span className="hl-square" style={{ background: TONE_SQUARES[item.tone] }} />
                  <h3>{item.title}</h3>
                </div>
                <div className="hl-value">{item.value}</div>
                <div className="hl-sub">{item.sub}</div>
              </div>
            ))}
          </div>
        </section>
      </Reveal>

      {records.length > 0 && (
        <Reveal>
          <section className="landing-section">
            <div className="landing-section-head">
              <h2>
                <span className="hl-square" style={{ background: TONE_SQUARES.yellow }} />
                最新快照
              </h2>
              <Link href="/overview" className="landing-more">
                查看全部 →
              </Link>
            </div>
            <SnapshotPreview records={records.slice(0, 6)} />
          </section>
        </Reveal>
      )}

      <Reveal>
        <section className="landing-section">
          <div className="landing-section-head">
            <h2>
              <span className="hl-square" style={{ background: TONE_SQUARES.blue }} />
              监控中的站点
            </h2>
            <ComingSoon
              label="提交监控站点"
              variant="text"
              title="提交监控站点"
              description="站点录入功能即将上线：届时可以直接填写中转站的价格接口地址，加入监控清单。当前仍可通过仓库内的配置文件添加。"
            />
          </div>
          {sites.length > 0 ? (
            <div className="site-grid">
              {sites.map((site) => {
                const info = getSiteInfo(site.id, site.sourceUrl);
                const href = info.homepage || site.sourceUrl || "";
                return (
                  <div key={site.id} className="site-card">
                    <span
                      aria-hidden
                      className="site-dot"
                      style={{ background: site.enabled ? "var(--accent)" : "var(--text-3)" }}
                    />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="site-name">
                        {href ? (
                          <RiskLink href={href} variant="site">
                            {info.name}
                          </RiskLink>
                        ) : (
                          info.name
                        )}
                      </div>
                      <div className="site-count">
                        {site.models} 个模型{site.enabled ? "" : " · 已停用"}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <p style={{ color: "var(--text-2)" }}>还没有站点数据。</p>
          )}
        </section>
      </Reveal>

      <Reveal>
        <section className="landing-section">
          <div className="landing-section-head">
            <h2>
              <span className="hl-square" style={{ background: TONE_SQUARES.green }} />
              价格如何取证
            </h2>
          </div>
          <LandingFeatures />
        </section>
      </Reveal>
    </div>
  );
}
