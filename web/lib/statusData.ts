/** 渠道检测数据统一取数入口：凡是要展示渠道统计的页面（首页、总览、站点详情）
 *  都从这里取，口径只有一份——每站最近 400 条、近 7 天。
 *  曾经三个页面三种取法：首页全局 max_records 抽样、总览全局最近 600 条，
 *  抽样按总 id 取模不保站内密度，会丢渠道、漂移最新可用率
 *  （134-175-71 实测：首页比详情页少 grok-4.6 渠道、可用率 86% vs 75%）。
 *  现在逐站按详情页同参数拉取，数据同源，数字不可能再分叉。 */
import { apiGet } from "./api";
import { buildSiteViews, type SiteViews } from "./channelStatus";
import type { StatusSnapshot } from "./types";

/** 与站点详情页同款：每站最近 400 条。 */
export const STATUS_PER_SITE_LIMIT = 400;

/** 窗口固定近 7 天。 */
const STATUS_WINDOW_SECONDS = 7 * 86_400;

/** since 对齐到分钟：URL 稳定便于日志排查（原首页/详情页同款表达式，收拢到这里）。 */
export function statusSince(): number {
  return Math.floor((Date.now() / 1000 - STATUS_WINDOW_SECONDS) / 60) * 60;
}

/** 单站拉取：参数与站点详情页逐字相同，是「详情页口径」的本体。 */
export async function fetchSiteStatus(
  siteId: string,
  headers?: HeadersInit,
): Promise<StatusSnapshot[]> {
  const data = await apiGet<{ records: StatusSnapshot[] }>(
    `/api/status?site_id=${encodeURIComponent(siteId)}&limit=${STATUS_PER_SITE_LIMIT}&since=${statusSince()}`,
    headers,
  );
  return data.records ?? [];
}

/** 全站视图：站点清单取 /api/status/latest（只含采过状态的站），逐站并发拉详情页口径，
 *  单站失败只缺该站、不拖垮整页；合并后跑一次 buildSiteViews，各站视图共享同一份输入。 */
export async function loadStatusViews(headers?: HeadersInit): Promise<SiteViews> {
  const latest = await apiGet<Record<string, unknown>>("/api/status/latest", headers).catch(
    () => ({} as Record<string, unknown>),
  );
  const siteIds = Object.keys(latest);
  const batches = await Promise.all(
    siteIds.map((siteId) => fetchSiteStatus(siteId, headers).catch(() => [] as StatusSnapshot[])),
  );
  return buildSiteViews(batches.flat());
}
