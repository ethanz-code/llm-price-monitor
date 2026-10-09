import { apiGetOptional } from "@/lib/api";
import type { CatalogData } from "@/lib/types";
import { PageDigest } from "@/components/PageDigest";
import { SiteAlert } from "@/components/SiteAlert";
import { CatalogView } from "@/components/CatalogView";
import { alerts } from "@/lib/copy";

export const dynamic = "force-dynamic";

export const metadata = { title: "厂商定价" };

export default async function CatalogPage({
  searchParams,
}: {
  searchParams: Promise<{ view?: string }>;
}) {
  const { view } = await searchParams;
  let official: CatalogData | null = null;
  let all: CatalogData | null = null;
  let error: string | null = null;
  try {
    [official, all] = await Promise.all([
      apiGetOptional<CatalogData>("/api/catalog"),
      apiGetOptional<CatalogData>("/api/catalog/all"),
    ]);
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }

  return (
    <div className="page">
      {official && (
        <PageDigest
          items={[
            { label: "官方定价模型", value: String(Object.keys(official.models).length) },
            ...(all ? [{ label: "全量渠道模型", value: String(Object.keys(all.models).length) }] : []),
          ]}
        />
      )}
      {error && (
        <SiteAlert title={alerts.catalog.title} detail={error} fix={alerts.catalog.fix} />
      )}
      <CatalogView official={official} all={all} initialView={view === "all" ? "all" : "official"} />
    </div>
  );
}
