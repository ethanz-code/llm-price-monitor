import { apiGet } from "@/lib/api";
import type { DiscountData } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { SiteAlert } from "@/components/SiteAlert";
import { DiscountTable } from "@/components/DiscountTable";

export const dynamic = "force-dynamic";

export const metadata = { title: "折扣对比" };

export default async function DiscountPage() {
  let data: DiscountData | null = null;
  let error: string | null = null;
  try {
    data = await apiGet<DiscountData>("/api/discount");
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }

  return (
    <div className="page">
      <PageHeader
        eyebrow="DISCOUNTS"
        title="折扣对比"
        subtitle="站点价折算 CNY 后与厂商官方原价相除：比值 19% 即“1.9 折”，越低越便宜。"
      />
      {error && (
        <SiteAlert
          title="无法计算折扣"
          detail={error}
          fix="需要已生成的官方价文件（fetch-official-prices）与最新价格快照"
        />
      )}
      {data && <DiscountTable data={data} />}
    </div>
  );
}
