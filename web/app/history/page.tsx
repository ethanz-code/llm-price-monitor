import { apiGet } from "@/lib/api";
import type { FeedData, HistoryListData } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { SiteAlert } from "@/components/SiteAlert";
import { HistoryView } from "@/components/HistoryView";
import { alerts, subtitles } from "@/lib/copy";

export const dynamic = "force-dynamic";

export const metadata = { title: "事件追踪" };

export default async function HistoryPage() {
  let feed: FeedData | null = null;
  let history: HistoryListData | null = null;
  let error: string | null = null;
  try {
    // 事件流已由 /api/feed 统一合并（价格+公告），历史记录独立取
    [feed, history] = await Promise.all([
      apiGet<FeedData>("/api/feed?events_limit=300&notice_limit=100"),
      apiGet<HistoryListData>("/api/history?limit=500"),
    ]);
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }

  return (
    <div className="page">
      <PageHeader
        title="事件追踪"
        subtitle={subtitles.history}
      />
      {error && <SiteAlert title={alerts.loadData.title} detail={error} fix={alerts.loadData.fix} />}
      {feed && history && (
        <HistoryView
          feed={feed.events}
          priceTotal={feed.price_total}
          noticeTotal={feed.notice_total}
          history={history}
        />
      )}
    </div>
  );
}
