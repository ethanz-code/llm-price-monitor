import { apiGet } from "@/lib/api";
import type { OverviewData } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { OverviewTable } from "@/components/OverviewTable";
import { SiteAlert } from "@/components/SiteAlert";

export const dynamic = "force-dynamic";

export const metadata = { title: "价格总览" };

export default async function OverviewPage() {
  let data: OverviewData | null = null;
  let error: string | null = null;
  try {
    data = await apiGet<OverviewData>("/api/overview");
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }

  return (
    <div className="page">
      <PageHeader
        eyebrow="OVERVIEW"
        title="价格总览"
        subtitle="各中转站最新一次采集的模型单价，折扣为站点价相对厂商官方原价的比值——越低越便宜。"
      />
      {error && <SiteAlert title="无法读取监控数据" detail={error} fix="请确认后端已启动：uv run price-web" />}
      {data && <OverviewTable data={data} />}
    </div>
  );
}
