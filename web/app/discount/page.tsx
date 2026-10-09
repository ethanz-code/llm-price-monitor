import { cookies } from "next/headers";
import { apiGet } from "@/lib/api";
import { channelDotsBySite, type ChannelDotRow } from "@/lib/channelStatus";
import type { DiscountData, StatusSnapshot } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { SiteAlert } from "@/components/SiteAlert";
import { DiscountTable } from "@/components/DiscountTable";
import { alerts, subtitles } from "@/lib/copy";

export const dynamic = "force-dynamic";

export const metadata = { title: "折扣对比" };

const SESSION_COOKIE = "ppm_session";

// 与总览页同一口径：渠道迷你方块取最近 30 次检测，600 条时序足够
const STATUS_LIMIT = 600;

export default async function DiscountPage() {
  const session = (await cookies()).get(SESSION_COOKIE)?.value;
  const headers = session ? { Cookie: `${SESSION_COOKIE}=${session}` } : undefined;
  let data: DiscountData | null = null;
  // 渠道点阵数据源；拉取失败只影响该列展示，不阻塞折扣页
  let statusDots: Record<string, ChannelDotRow[]> = {};
  let error: string | null = null;
  try {
    const [discount, status] = await Promise.all([
      apiGet<DiscountData>("/api/discount"),
      apiGet<{ records: StatusSnapshot[] }>(`/api/status?limit=${STATUS_LIMIT}`, headers).catch(() => null),
    ]);
    data = discount;
    statusDots = status ? channelDotsBySite(status.records ?? []) : {};
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }

  return (
    <div className="page">
      <PageHeader
        eyebrow="DISCOUNTS"
        title="折扣对比"
        subtitle={subtitles.discount}
      />
      {error && (
        <SiteAlert title={alerts.discount.title} detail={error} fix={alerts.discount.fix} />
      )}
      {data && <DiscountTable data={data} statusDots={statusDots} />}
    </div>
  );
}
