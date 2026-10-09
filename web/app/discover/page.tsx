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
  "自动发现的中转站清单：公开价格接口可用性、模型数量与监控状态，找新中转站先看这里。",
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
            description: row.description || row.system_name || undefined,
          })),
        }
      : null;

  return (
    <div className="page">
      {jsonLd && <JsonLd data={jsonLd} />}
      {data && (
        <PageDigest
          items={[
            { label: "可用中转站", value: formatCount(data.summary.available) },
            { label: "已在监控", value: formatCount(data.summary.imported) },
            { label: "待监控", value: formatCount(data.summary.available - data.summary.imported) },
            { label: "需登录/失联", value: formatCount(data.summary.auth + data.summary.dead) },
          ]}
        />
      )}
      {notReady && <SiteAlert title={alerts.discovery.title} detail="" fix={alerts.discovery.fix} />}
      {data && data.stations.length === 0 && (
        <SiteAlert title={alerts.discovery.title} detail="" fix={alerts.discovery.fix} />
      )}
      {data && data.stations.length > 0 && <DiscoverTable data={data} />}
    </div>
  );
}
