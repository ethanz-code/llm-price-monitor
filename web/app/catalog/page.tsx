import { apiGetOptional } from "@/lib/api";
import type { CatalogData } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { SiteAlert } from "@/components/SiteAlert";
import { CatalogView } from "@/components/CatalogView";
import { alerts, subtitles } from "@/lib/copy";

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
      <PageHeader
        title="厂商定价"
        subtitle={subtitles.catalog}
      />
      {error && (
        <SiteAlert title={alerts.catalog.title} detail={error} fix={alerts.catalog.fix} />
      )}
      <CatalogView official={official} all={all} initialView={view === "all" ? "all" : "official"} />
    </div>
  );
}
