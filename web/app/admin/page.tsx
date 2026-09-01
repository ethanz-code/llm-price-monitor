import { apiGet } from "@/lib/api";
import { formatDiscount } from "@/lib/format";
import type { EventListData, OverviewData } from "@/lib/types";
import { AdminPanels } from "@/components/AdminPanels";
import { Alert } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { SiteAlert } from "@/components/SiteAlert";

export const dynamic = "force-dynamic";

export const metadata = { title: "管理面板" };

export default async function AdminPage() {
  let overview: OverviewData | null = null;
  let events: EventListData | null = null;
  let error: string | null = null;
  try {
    [overview, events] = await Promise.all([
      apiGet<OverviewData>("/api/overview"),
      apiGet<EventListData>("/api/events?limit=8"),
    ]);
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }

  const records = overview?.records ?? [];
  const siteIds = new Set(records.map((row) => row.site_id));
  const inputs = records.map((row) => row.discount?.input).filter((v): v is number => v !== null && v !== undefined);
  const avgInput = inputs.length ? inputs.reduce((a, b) => a + b, 0) / inputs.length : null;

  return (
    <div className="page">
      <PageHeader
        eyebrow="ADMIN"
        title="管理面板"
        subtitle="站点配置、采集任务、事件审计与系统设置的统一管理入口。"
      />
      <Alert tone="info" title="管理员区域" className="section-gap">
        公开数据无需登录即可浏览；本页的站点配置、系统设置与采集触发仅限管理员——首次访问会弹出登录框，
        输入管理员密码后即拥有全部管理能力。
      </Alert>
      {error && <SiteAlert title="无法读取监控数据" detail={error} fix="请确认后端已启动：uv run price-web" />}
      <AdminPanels
        kpis={{
          sites: siteIds.size,
          records: records.length,
          events: events?.total ?? 0,
          avgInput,
        }}
        events={events?.events ?? []}
      />
    </div>
  );
}
