import { apiGet } from "@/lib/api";
import { formatDiscount } from "@/lib/format";
import { getSiteInfo } from "@/lib/sites";
import type { EventListData, MetaData, OverviewData } from "@/lib/types";
import { AdminPanels } from "@/components/AdminPanels";
import { Alert } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { SiteAlert } from "@/components/SiteAlert";

export const dynamic = "force-dynamic";

export const metadata = { title: "管理面板" };

export default async function AdminPage() {
  let overview: OverviewData | null = null;
  let meta: MetaData | null = null;
  let events: EventListData | null = null;
  let error: string | null = null;
  try {
    [overview, meta, events] = await Promise.all([
      apiGet<OverviewData>("/api/overview"),
      apiGet<MetaData>("/api/meta"),
      apiGet<EventListData>("/api/events?limit=8"),
    ]);
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }

  const records = overview?.records ?? [];
  const siteIds = new Set(records.map((row) => row.site_id));
  const inputs = records.map((row) => row.discount?.input).filter((v): v is number => v !== null && v !== undefined);
  const avgInput = inputs.length ? inputs.reduce((a, b) => a + b, 0) / inputs.length : null;

  const sites = (meta?.sites ?? []).map((site) => ({
    id: site.id,
    name: getSiteInfo(site.id, site.url).name,
    models: site.models.length,
    enabled: site.enabled,
  }));

  return (
    <div className="page">
      <PageHeader
        eyebrow="ADMIN"
        title="管理面板"
        subtitle="站点清单、采集任务与事件审计的统一管理入口。"
      />
      <Alert tone="info" title="管理面板预览" className="section-gap" >
        登录体系尚未上线：当前页面用于呈现管理面板的形态与交互，其中站点启停、设置保存等写操作会提示预览模式；「立即采集」为真实功能，点击即触发采集任务。正式版将通过账号鉴权访问本页。
      </Alert>
      {error && <SiteAlert title="无法读取监控数据" detail={error} fix="请确认后端已启动：uv run price-web" />}
      <AdminPanels
        sites={sites}
        kpis={{
          sites: siteIds.size || (meta?.sites.length ?? 0),
          records: records.length,
          events: events?.total ?? 0,
          avgInput,
        }}
        events={events?.events ?? []}
      />
    </div>
  );
}
