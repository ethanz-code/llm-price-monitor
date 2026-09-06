import Link from "next/link";
import { Button } from "antd";
import { apiGet } from "@/lib/api";
import { formatDiscount } from "@/lib/format";
import { getSiteInfo } from "@/lib/sites";
import type { MetaData, OverviewData } from "@/lib/types";
import { RiskLink } from "@/components/RiskLink";
import { ComingSoon } from "@/components/ComingSoon";
import { LandingFeatures } from "@/components/LandingFeatures";
import { SnapshotPreview } from "@/components/SnapshotPreview";
import { SiteAlert } from "@/components/SiteAlert";

export const dynamic = "force-dynamic";

interface LandingData {
  overview: OverviewData | null;
  meta: MetaData | null;
  error: string | null;
}

async function loadLanding(): Promise<LandingData> {
  try {
    const [overview, meta] = await Promise.all([
      apiGet<OverviewData>("/api/overview"),
      apiGet<MetaData>("/api/meta"),
    ]);
    return { overview, meta, error: null };
  } catch (cause) {
    return { overview: null, meta: null, error: cause instanceof Error ? cause.message : String(cause) };
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
  const { overview, meta, error } = await loadLanding();
  const records = overview?.records ?? [];
  const sites = collectSites(overview, meta);

  const siteIds = new Set(records.map((row) => row.site_id));
  const modelIds = new Set(records.map((row) => row.model));
  const inputs = records.map((row) => row.discount?.input).filter((v): v is number => v !== null && v !== undefined);
  const avgInput = inputs.length ? inputs.reduce((a, b) => a + b, 0) / inputs.length : null;

  const stats = [
    { value: String(siteIds.size || "—"), label: "监控站点" },
    { value: String(modelIds.size || "—"), label: "跟踪模型" },
    { value: String(records.length || "—"), label: "价格记录" },
    { value: avgInput !== null ? formatDiscount(avgInput) : "—", label: "平均输入折扣" },
  ];

  return (
    <div className="page landing">
      <section className="hero">
        <div className="page-eyebrow">LLM PRICE MONITOR</div>
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
            <Button type="primary" size="large">
              进入价格总览 →
            </Button>
          </Link>
          <Link href="/discount">
            <Button size="large">查看折扣对比</Button>
          </Link>
        </div>
        <div className="hero-stats">
          {stats.map((item) => (
            <div key={item.label} className="hero-stat">
              <div className="hero-stat-value">{item.value}</div>
              <div className="hero-stat-label">{item.label}</div>
            </div>
          ))}
        </div>
      </section>

      {error && <SiteAlert title="无法读取监控数据" detail={error} fix="请确认后端已启动：uv run price-web" />}

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
            type="text"
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

      <section className="landing-section">
        <div className="landing-section-head">
          <h2>价格如何取证</h2>
        </div>
        <LandingFeatures />
      </section>
    </div>
  );
}
