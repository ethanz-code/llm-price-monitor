import Link from "next/link";
import { apiGet } from "@/lib/api";
import { formatDiscount } from "@/lib/format";
import type { EventListData, OverviewData } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { SiteAlert } from "@/components/SiteAlert";
import { IconAppstore, IconBolt, IconKey, IconPlus } from "@/components/icons";

export const dynamic = "force-dynamic";

export const metadata = { title: "管理面板 · 概览" };

/** 概览：KPI 统计 + 新手入门清单（尚无站点时显示）。 */
export default async function AdminOverviewPage() {
  let overview: OverviewData | null = null;
  let events: EventListData | null = null;
  let error: string | null = null;
  try {
    [overview, events] = await Promise.all([
      apiGet<OverviewData>("/api/overview"),
      apiGet<EventListData>("/api/events?limit=1"),
    ]);
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }

  const records = overview?.records ?? [];
  const siteIds = new Set(records.map((row) => row.site_id));
  const inputs = records.map((row) => row.discount?.input).filter((v): v is number => v !== null && v !== undefined);
  const avgInput = inputs.length ? inputs.reduce((a, b) => a + b, 0) / inputs.length : null;

  const kpis = [
    { label: "监控站点", value: String(siteIds.size), hint: "来自站点配置" },
    { label: "价格记录", value: String(records.length), hint: "自首次采集累计" },
    { label: "事件总数", value: String(events?.total ?? 0), hint: "新增与变化合计" },
    {
      label: "平均输入折扣",
      value: avgInput !== null ? formatDiscount(avgInput) : "—",
      hint: "相对厂商官方价",
    },
  ];

  return (
    <>
      <PageHeader
        eyebrow="ADMIN · OVERVIEW"
        title="概览"
        subtitle="监控规模与采集结果的总体情况。"
      />
      {error && <SiteAlert title="无法读取监控数据" detail={error} fix="请确认后端已启动：uv run price-web" />}
      <div className="stat-grid section-gap">
        {kpis.map((card) => (
          <div key={card.label} className="stat-card">
            <div className="stat-label">{card.label}</div>
            <div className="stat-value">{card.value}</div>
            <div className="stat-hint">{card.hint}</div>
          </div>
        ))}
      </div>

      {siteIds.size === 0 && !error && (
        <section className="guide section-gap">
          <h2>从这里开始</h2>
          <p className="guide-sub">三步跑通第一轮价格取证：</p>
          <ol className="guide-list">
            <li>
              <span className="guide-icon"><IconPlus size={15} /></span>
              <div>
                <div className="guide-title">1 · 添加监控站点</div>
                <p>填写中转站的价格接口地址与目标模型，支持请求头、分组倍率等高级配置。</p>
              </div>
              <Link href="/admin/sites" className="guide-link">去添加 →</Link>
            </li>
            <li>
              <span className="guide-icon"><IconKey size={15} /></span>
              <div>
                <div className="guide-title">2 · 配好 AI 与 Tavily</div>
                <p>AI 兜底提取负责解析格式不明的站点；Tavily 用于检索厂商官方定价页，折扣对比需要它。</p>
              </div>
              <Link href="/admin/settings" className="guide-link">去设置 →</Link>
            </li>
            <li>
              <span className="guide-icon"><IconBolt size={15} /></span>
              <div>
                <div className="guide-title">3 · 触发首次采集</div>
                <p>先预览一轮确认解析结果，再勾选写入历史；之后每次采集都会沉淀事件与曲线。</p>
              </div>
              <Link href="/admin/tasks" className="guide-link">去采集 →</Link>
            </li>
          </ol>
          <p className="guide-foot">
            <IconAppstore size={13} /> 公开页面（总览 / 历史 / 官方价 / 折扣）无需登录即可浏览。
          </p>
        </section>
      )}
    </>
  );
}
