import { isNoticeEvent } from "@/lib/format";
import type { EventRow, FeedEvent } from "@/lib/types";

/**
 * 同一轮扫描（检测时间相邻 120 秒内）同站点+模型+同类事件折成一组，多分组只算一张卡；公告事件不折叠。
 * 首页「最新事件」与事件追踪页共用同一口径：事件按「站点+模型+分组」粒度入库，同模型多分组
 * 不折叠的话会并排出几条看起来一模一样的行，两页各自处理又会互相对不上。
 * 入参须是 /api/feed 已按 detected_at 倒序的统一事件流（折叠只看相邻事件）。
 */
export function foldEvents(events: FeedEvent[]): FeedEvent[][] {
  const groups: FeedEvent[][] = [];
  for (const event of events) {
    const last = groups[groups.length - 1];
    const lastEvent = last?.[last.length - 1];
    if (
      last &&
      lastEvent &&
      !isNoticeEvent(event) &&
      !isNoticeEvent(lastEvent) &&
      lastEvent.site_id === event.site_id &&
      lastEvent.model === event.model &&
      lastEvent.kind === event.kind &&
      Math.abs(event.detected_at - lastEvent.detected_at) <= 120
    ) {
      last.push(event);
    } else {
      groups.push([event]);
    }
  }
  return groups;
}

/** 折叠组内各事件的分组名（current 优先，退回 previous；公告组返回空数组）。 */
export function groupNames(group: FeedEvent[]): string[] {
  return group
    .filter((event): event is EventRow => !isNoticeEvent(event))
    .map(
      (event) =>
        (event.current?.metadata?.group || event.previous?.metadata?.group) ?? null,
    )
    .filter((name): name is string => name != null);
}
