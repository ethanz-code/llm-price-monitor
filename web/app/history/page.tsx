import { apiGet } from "@/lib/api";
import type { EventListData, HistoryListData } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { SiteAlert } from "@/components/SiteAlert";
import { HistoryView } from "@/components/HistoryView";

export const dynamic = "force-dynamic";

export const metadata = { title: "历史与事件" };

export default async function HistoryPage() {
  let events: EventListData | null = null;
  let history: HistoryListData | null = null;
  let error: string | null = null;
  try {
    [events, history] = await Promise.all([
      apiGet<EventListData>("/api/events?limit=300"),
      apiGet<HistoryListData>("/api/history?limit=500"),
    ]);
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }

  return (
    <div className="page">
      <PageHeader
        eyebrow="TIMELINE"
        title="历史与事件"
        subtitle="每次采集的完整记录与检测到的变化事件：新增、涨价、降价、恢复、状态变化，附完整证据链。"
      />
      {error && <SiteAlert title="无法读取监控数据" detail={error} fix="请确认后端已启动：uv run price-web" />}
      {events && history && <HistoryView events={events} history={history} />}
    </div>
  );
}
