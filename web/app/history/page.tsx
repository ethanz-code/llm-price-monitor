import { apiGet } from "@/lib/api";
import type { EventListData, HistoryListData, NoticeEventListData } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { SiteAlert } from "@/components/SiteAlert";
import { HistoryView } from "@/components/HistoryView";

export const dynamic = "force-dynamic";

export const metadata = { title: "历史与事件" };

export default async function HistoryPage() {
  let events: EventListData | null = null;
  let history: HistoryListData | null = null;
  let noticeEvents: NoticeEventListData | null = null;
  let error: string | null = null;
  try {
    [events, history, noticeEvents] = await Promise.all([
      apiGet<EventListData>("/api/events?limit=300"),
      apiGet<HistoryListData>("/api/history?limit=500"),
      // 公告事件拉取失败只影响事件流里的公告条目，不阻塞整页
      apiGet<NoticeEventListData>("/api/notice/events?limit=100").catch(() => ({ events: [], total: 0 })),
    ]);
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }

  return (
    <div className="page">
      <PageHeader
        eyebrow="TIMELINE"
        title="历史与事件"
        subtitle="每次采集的完整记录与检测到的变化事件：价格增减、状态变化、站点公告，每条都能溯源到原始页面。"
      />
      {error && <SiteAlert title="暂时读不到监控数据" detail={error} fix="请稍后刷新重试；若持续出现，欢迎通过页脚「提建议」告诉我们。" />}
      {events && history && (
        <HistoryView
          events={events}
          history={history}
          noticeEvents={noticeEvents?.events ?? []}
          noticeTotal={noticeEvents?.total ?? 0}
        />
      )}
    </div>
  );
}
