import { apiGet } from "@/lib/api";
import { eventMeta, formatTime } from "@/lib/format";
import { getSiteInfo } from "@/lib/sites";
import type { EventListData, EventRow } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { SiteAlert } from "@/components/SiteAlert";
import { CollectButton } from "@/components/CollectButton";

export const dynamic = "force-dynamic";

export const metadata = { title: "管理面板 · 事件审计" };

export default async function AdminEventsPage() {
  let events: EventListData | null = null;
  let error: string | null = null;
  try {
    events = await apiGet<EventListData>("/api/events?limit=30");
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }

  const rows = events?.events ?? [];

  return (
    <>
      <PageHeader
        eyebrow="ADMIN · EVENTS"
        title="事件审计"
        subtitle="采集过程中发现的每一处变化，最多展示最近 30 条。"
      />
      {error && <SiteAlert title="无法读取事件数据" detail={error} fix="请确认后端已启动：uv run price-web" />}
      <div className="panel section-gap" style={{ padding: "24px 28px" }}>
        {rows.length > 0 ? (
          <ol className="tline">
            {rows
              .slice(-30)
              .reverse()
              .map((event: EventRow) => {
                const meta = eventMeta(event.kind);
                const site = getSiteInfo(event.site_id, event.current?.source_url ?? event.previous?.source_url);
                return (
                  <li key={`${event.site_id}:${event.model}:${event.detected_at}`}>
                    <span className="tdot" style={{ background: `var(--tone-${meta.tone}-text)` }} />
                    <span style={{ fontSize: 13 }}>
                      <span className="mono" style={{ fontWeight: 550 }}>{site.name}</span>
                      {" · "}
                      {meta.label}
                      {" · "}
                      <span className="mono" style={{ color: "var(--text-2)" }}>{event.model}</span>
                      <span style={{ color: "var(--text-3)", marginLeft: 8 }}>{formatTime(event.detected_at)}</span>
                    </span>
                  </li>
                );
              })}
          </ol>
        ) : (
          <span style={{ color: "var(--text-2)", fontSize: 13 }}>还没有事件记录；触发一轮采集后变化会出现在这里。</span>
        )}
      </div>
    </>
  );
}
