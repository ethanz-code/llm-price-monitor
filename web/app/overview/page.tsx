import { cookies } from "next/headers";
import { apiGet } from "@/lib/api";
import { channelDotsBySite, type ChannelDotRow } from "@/lib/channelStatus";
import type { OverviewData, StatusSnapshot } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { alerts, subtitles } from "@/lib/copy";
import { OverviewTable } from "@/components/OverviewTable";
import { SiteAlert } from "@/components/SiteAlert";

export const dynamic = "force-dynamic";

export const metadata = { title: "中转站定价" };

// collect_status（需要关注的站点）仅管理员可见：服务端请求必须带上会话 cookie
const SESSION_COOKIE = "ppm_session";

// 渠道迷你方块取最近 30 次检测；600 条时序对多站点场景足够，摘要化后传给前端体积很小
const STATUS_LIMIT = 600;

export default async function OverviewPage() {
  const session = (await cookies()).get(SESSION_COOKIE)?.value;
  const headers = session ? { Cookie: `${SESSION_COOKIE}=${session}` } : undefined;
  let data: OverviewData | null = null;
  // 渠道点阵数据源（渠道 → 检测点序列）；拉取失败只影响该列展示，不阻塞总览
  let statusDots: Record<string, ChannelDotRow[]> = {};
  let error: string | null = null;
  try {
    const [overview, status] = await Promise.all([
      apiGet<OverviewData>("/api/overview", headers),
      apiGet<{ records: StatusSnapshot[] }>(`/api/status?limit=${STATUS_LIMIT}`, headers).catch(() => null),
    ]);
    data = overview;
    statusDots = status ? channelDotsBySite(status.records ?? []) : {};
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }

  return (
    <div className="page">
      <PageHeader
        eyebrow="OVERVIEW"
        title="中转站定价"
        subtitle={subtitles.overview}
      />
      {error && <SiteAlert title={alerts.loadData.title} detail={error} fix={alerts.loadData.fix} />}
      {data && <OverviewTable data={data} statusDots={statusDots} />}
    </div>
  );
}
