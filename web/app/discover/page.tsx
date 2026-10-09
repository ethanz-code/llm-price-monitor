import Link from "next/link";
import { apiGetOptional, PUBLIC_REVALIDATE } from "@/lib/api";
import type { DiscoveryData } from "@/lib/types";
import { PageDigest } from "@/components/PageDigest";
import { SiteAlert } from "@/components/SiteAlert";
import { DiscoverTable } from "@/components/DiscoverTable";
import { JsonLd } from "@/components/JsonLd";
import { alerts } from "@/lib/copy";
import { formatCount } from "@/lib/format";
import { pageMetadata, siteOrigin } from "@/lib/seo";

export const dynamic = "force-dynamic";

export const metadata = pageMetadata(
  "新站发现",
  "自动收录的公开中转站清单：站点简介、7 天可用率与响应耗时，找新中转站先看这里。",
  "/discover",
);

export default async function DiscoverPage() {
  let data: DiscoveryData | null = null;
  let notReady = false;
  try {
    data = await apiGetOptional<DiscoveryData>("/api/discovery", PUBLIC_REVALIDATE);
  } catch {
    notReady = true;
  }

  const origin = await siteOrigin();
  // 结构化数据：给搜索与 AI 摘要一份机器可读的站点清单（只放前 100 个，避免无界输出）
  const jsonLd =
    data && data.stations.length > 0
      ? {
          "@context": "https://schema.org",
          "@type": "ItemList",
          name: "发现的中转站清单",
          numberOfItems: data.stations.length,
          itemListElement: data.stations.slice(0, 100).map((row, index) => ({
            "@type": "ListItem",
            position: index + 1,
            name: row.host,
            url: row.url,
            description: row.description || undefined,
          })),
        }
      : null;

  return (
    <div className="page">
      <h1 className="sr-only">新站发现</h1>
      {jsonLd && <JsonLd data={jsonLd} />}
      {data && (
        <PageDigest
          items={[
            { label: "已收录站点", value: formatCount(data.summary.total), title: "自动收录的中转站总数" },
            { label: "已在监控", value: formatCount(data.summary.imported), title: "已导入本站监控的站点数量" },
          ]}
        />
      )}
      {notReady && <SiteAlert title={alerts.discovery.title} detail="" fix={alerts.discovery.fix} />}
      {data && data.stations.length === 0 && (
        <SiteAlert title={alerts.discovery.title} detail="" fix={alerts.discovery.fix} />
      )}
      {data && data.stations.length > 0 && <DiscoverTable data={data} />}

      {/* 发现的下一步：事件追踪入口。这些站接入监控后，价格与公告的变化都汇总在事件流里 */}
      <div
        style={{
          marginTop: 28,
          padding: "24px 28px",
          background: "var(--panel)",
          border: "var(--card-border)",
          borderRadius: 12,
          display: "flex",
          flexWrap: "wrap",
          alignItems: "baseline",
          gap: "6px 20px",
        }}
      >
        <h2 style={{ margin: 0, fontSize: 17, fontWeight: 600 }}>事件追踪</h2>
        <p style={{ margin: 0, fontSize: 13.5, color: "var(--text-2)", flex: 1, minWidth: 240 }}>
          站点接入监控后，降价、涨价、新增与公告变化都汇总在事件流里，全部留档可回查。
        </p>
        <Link href="/history" style={{ fontSize: 13.5, color: "var(--accent-text)", textDecoration: "none", whiteSpace: "nowrap" }}>
          进入事件追踪 →
        </Link>
      </div>
    </div>
  );
}
