import { apiGetOptional } from "@/lib/api";
import type { CatalogData } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { SiteAlert } from "@/components/SiteAlert";
import { CatalogView } from "@/components/CatalogView";

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
        eyebrow="MODEL CATALOG"
        title="厂商定价"
        subtitle="数据来自开源模型目录 models.dev，价格统一为美元并按快照汇率换算成人民币，是折扣对比的基准；每条价格都能点开来源核对。切到「全量渠道」可看 OpenRouter 等全部渠道的价格。"
      />
      {error && (
        <SiteAlert
          title="无法读取厂商定价"
          detail={error}
          fix="请稍后刷新重试；若持续出现，欢迎通过页脚「提建议」告诉我们。"
        />
      )}
      <CatalogView official={official} all={all} initialView={view === "all" ? "all" : "official"} />
    </div>
  );
}
