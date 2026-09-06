import { apiGet } from "@/lib/api";
import type { OfficialData } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { SiteAlert } from "@/components/SiteAlert";
import { OfficialTable } from "@/components/OfficialTable";

export const dynamic = "force-dynamic";

export const metadata = { title: "官方价库" };

export default async function OfficialPage() {
  let data: OfficialData | null = null;
  let error: string | null = null;
  try {
    data = await apiGet<OfficialData>("/api/official");
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }

  return (
    <div className="page">
      <PageHeader
        eyebrow="OFFICIAL PRICES"
        title="官方价库"
        subtitle="通过 Tavily 搜索厂商官方定价页，再用 AI 提取模型原价；折扣对比以 effective 价为基准，来源链接可溯源。"
      />
      {error && (
        <SiteAlert
          title="无法读取官方价"
          detail={error}
          fix="若尚未生成，可点击“刷新官方价”抓取，或运行 fetch-official-prices"
        />
      )}
      {data && <OfficialTable data={data} />}
    </div>
  );
}
