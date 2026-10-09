import { cookies } from "next/headers";
import { apiGet } from "@/lib/api";
import { channelDotsBySite, type ChannelDotRow } from "@/lib/channelStatus";
import { buildRankingIndex, type RankingHit } from "@/lib/rankings";
import { canonicalModel } from "@/lib/priceRows";
import type { CatalogData, OverviewData, RankingsData, StatusSnapshot } from "@/lib/types";
import { alerts } from "@/lib/copy";
import { formatCount } from "@/lib/format";
import { PageDigest } from "@/components/PageDigest";
import { OverviewTable } from "@/components/OverviewTable";
import { SiteAlert } from "@/components/SiteAlert";
import { pageMetadata } from "@/lib/seo";

export const dynamic = "force-dynamic";

export const metadata = pageMetadata(
  "中转站定价",
  "各家中转站的模型输入输出价格与折扣对照，附渠道可用率，每条价格都附来源链接，点开就能核对。",
  "/overview",
);

// collect_status（需要关注的站点）仅管理员可见：服务端请求必须带上会话 cookie。
// 因此本页保持逐请求渲染 + 取数 no-store，不走 PUBLIC_REVALIDATE——
// 管理员与匿名访客的取数结果不能共享同一份缓存
const SESSION_COOKIE = "ppm_session";

// 渠道迷你方块取最近 30 次检测；600 条时序对多站点场景足够，摘要化后传给前端体积很小
const STATUS_LIMIT = 600;

export default async function OverviewPage() {
  const session = (await cookies()).get(SESSION_COOKIE)?.value;
  const headers = session ? { Cookie: `${SESSION_COOKIE}=${session}` } : undefined;
  let data: OverviewData | null = null;
  // 渠道点阵数据源（渠道 → 检测点序列）；拉取失败只影响该列展示，不阻塞总览
  let statusDots: Record<string, ChannelDotRow[]> = {};
  // AA 榜单匹配索引：给当前选中模型挂排名徽标；拉取失败只影响徽标
  let rankingsIndex: Record<string, RankingHit> = {};
  // 目录发布日期索引（模型归一键 → release_date）：模型下拉按「发布日期倒序」排的依据；
  // 只传这份轻量映射而非整个目录，拉取失败只影响排序口径（退化为按名称）
  let releaseByModel: Record<string, string> = {};
  let error: string | null = null;
  try {
    const [overview, status, rankings, catalog] = await Promise.all([
      apiGet<OverviewData>("/api/overview", headers),
      apiGet<{ records: StatusSnapshot[] }>(`/api/status?limit=${STATUS_LIMIT}`, headers).catch(() => null),
      apiGet<RankingsData>("/api/rankings").catch(() => null),
      apiGet<CatalogData>("/api/catalog").catch(() => null),
    ]);
    data = overview;
    statusDots = status ? channelDotsBySite(status.records ?? []) : {};
    rankingsIndex = buildRankingIndex(rankings);
    for (const entry of Object.values(catalog?.models ?? {})) {
      if (entry?.model) releaseByModel[canonicalModel(entry.model)] = entry.release_date ?? "";
    }
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }

  return (
    <div className="page">
      {/* 数据页不设可见页头（当前页由导航标识），仅给搜索引擎与读屏留隐藏 h1 */}
      <h1 className="sr-only">中转站定价</h1>
      {error && <SiteAlert title={alerts.loadData.title} detail={error} fix={alerts.loadData.fix} />}
      {data && (
        <PageDigest
          items={[
            { label: "站点", value: formatCount(new Set(data.records.map((row) => row.site_id)).size) },
            { label: "模型", value: formatCount(new Set(data.records.map((row) => row.model)).size) },
            { label: "价格记录", value: formatCount(data.records.length) },
          ]}
        />
      )}
      {data && (
        <OverviewTable data={data} statusDots={statusDots} rankingsIndex={rankingsIndex} releaseByModel={releaseByModel} />
      )}
    </div>
  );
}
