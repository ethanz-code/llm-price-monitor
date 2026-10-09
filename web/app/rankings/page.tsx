import { apiGetOptional } from "@/lib/api";
import type { RankingsData } from "@/lib/types";
import { PageDigest } from "@/components/PageDigest";
import { SiteAlert } from "@/components/SiteAlert";
import { RankingsTable } from "@/components/RankingsTable";
import { alerts } from "@/lib/copy";
import { formatCount } from "@/lib/format";
import { pageMetadata } from "@/lib/seo";

export const dynamic = "force-dynamic";

export const metadata = pageMetadata(
  "模型榜单",
  "模型智能指数榜单，先看模型能力档位，再对照各站价格挑性价比。",
  "/rankings",
);

export default async function RankingsPage() {
  let data: RankingsData | null = null;
  let error: string | null = null;
  try {
    data = await apiGetOptional<RankingsData>("/api/rankings");
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }

  return (
    <div className="page">
      <h1 className="sr-only">模型榜单</h1>
      {data && (
        <PageDigest
          items={[
            { label: "上榜模型", value: formatCount(data.models.length) },
            { label: "评测厂商", value: formatCount(new Set(data.models.map((m) => m.creator).filter(Boolean)).size) },
          ]}
        />
      )}
      {error && (
        <SiteAlert title={alerts.rankings.title} detail={error} fix={alerts.rankings.fix} />
      )}
      <RankingsTable data={data} />
    </div>
  );
}
