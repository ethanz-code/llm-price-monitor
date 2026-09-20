import { apiGetOptional } from "@/lib/api";
import { decodeCalcState, EMPTY_STATE } from "@/lib/calculator";
import { Calculator } from "@/components/Calculator";
import { PageDigest } from "@/components/PageDigest";
import { SiteAlert } from "@/components/SiteAlert";
import { calculator } from "@/lib/copy";
import type { CatalogData, OverviewData } from "@/lib/types";

export const dynamic = "force-dynamic";

export const metadata = { title: "花费计算" };

export default async function CalculatorPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (typeof value === "string") search.set(key, value);
  }
  // 两个价格源各自独立取数：任一不可用只影响对应模式，页面仍可手填单价使用
  const catalog = await apiGetOptional<CatalogData>("/api/catalog");
  const overview = await apiGetOptional<OverviewData>("/api/overview");
  const initial = search.size > 0 ? decodeCalcState(search) : EMPTY_STATE;

  return (
    <div className="page">
      {!catalog && !overview && (
        <SiteAlert
          title={calculator.loadFailed.title}
          detail="厂商定价与最新价格快照都还没准备好"
          fix={calculator.loadFailed.fix}
        />
      )}
      {catalog && (
        <PageDigest
          items={[
            { label: "官方价模型", value: String(Object.keys(catalog.models).length) },
            ...(overview ? [{ label: "站点", value: String(new Set(overview.records.map((row) => row.site_id)).size) }] : []),
            { label: "汇率", value: catalog.usd_cny_rate.toFixed(2) },
          ]}
        />
      )}
      <Calculator catalog={catalog} overview={overview} initial={initial} />
    </div>
  );
}
