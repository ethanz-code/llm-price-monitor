import { apiGetOptional } from "@/lib/api";
import type { CatalogData, RankingsData } from "@/lib/types";
import { buildRankingIndex } from "@/lib/rankings";
import { PageDigest } from "@/components/PageDigest";
import { SiteAlert } from "@/components/SiteAlert";
import { CatalogView } from "@/components/CatalogView";
import { alerts } from "@/lib/copy";
import { formatCount } from "@/lib/format";
import { pageMetadata } from "@/lib/seo";

export const dynamic = "force-dynamic";

export const metadata = pageMetadata(
  "厂商定价",
  "各家模型厂商的官方定价速查，官方价与全量渠道价并列对照，随官方页面同步更新。",
  "/catalog",
);

export default async function CatalogPage({
  searchParams,
}: {
  searchParams: Promise<{ view?: string }>;
}) {
  const { view } = await searchParams;
  let official: CatalogData | null = null;
  let all: CatalogData | null = null;
  let rankings: RankingsData | null = null;
  let error: string | null = null;
  try {
    // 榜单用于给目录条目补 AA 排名徽标；拉不到只影响徽标，不阻塞目录
    [official, all, rankings] = await Promise.all([
      apiGetOptional<CatalogData>("/api/catalog"),
      apiGetOptional<CatalogData>("/api/catalog/all"),
      apiGetOptional<RankingsData>("/api/rankings").catch(() => null),
    ]);
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }
  const rankingsIndex = buildRankingIndex(rankings);

  return (
    <div className="page">
      <h1 className="sr-only">厂商定价</h1>
      {official && (
        <PageDigest
          items={[
            { label: "官方定价模型", value: formatCount(Object.keys(official.models).length) },
            ...(all ? [{ label: "全量渠道模型", value: formatCount(Object.keys(all.models).length) }] : []),
          ]}
        />
      )}
      {error && (
        <SiteAlert title={alerts.catalog.title} detail={error} fix={alerts.catalog.fix} />
      )}
      <CatalogView official={official} all={all} rankingsIndex={rankingsIndex} initialView={view === "all" ? "all" : "official"} />
    </div>
  );
}
