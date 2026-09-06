import Link from "next/link";
import { apiGet } from "@/lib/api";
import { eventMeta, formatDiscount, formatTime } from "@/lib/format";
import { getSiteInfo } from "@/lib/sites";
import type { EventListData, OverviewData } from "@/lib/types";
import { SiteAlert } from "@/components/SiteAlert";
import {
  IconAim,
  IconAppstore,
  IconBook,
  IconBolt,
  IconFileSearch,
  IconKey,
  IconMonitor,
  IconPlus,
  IconSettings,
  IconSync,
} from "@/components/icons";
import { TermTip, type TermKey } from "@/components/TermTip";

export const dynamic = "force-dynamic";

export const metadata = { title: "管理面板 · 概览" };

/** 概览：双栏仪表盘 —— 主栏 KPI + 最近事件面板，右栏快捷入口与公开页面；尚无站点时显示入门清单。 */
export default async function AdminOverviewPage() {
  let overview: OverviewData | null = null;
  let events: EventListData | null = null;
  let error: string | null = null;
  try {
    [overview, events] = await Promise.all([
      apiGet<OverviewData>("/api/overview"),
      // 事件总数给 KPI 用，events 列表给「最近事件」面板用
      apiGet<EventListData>("/api/events?limit=8"),
    ]);
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }

  const records = overview?.records ?? [];
  const siteIds = new Set(records.map((row) => row.site_id));
  const latestEvents = events?.events.slice(-8).reverse() ?? [];
  const inputs = records.map((row) => row.discount?.input).filter((v): v is number => v !== null && v !== undefined);
  const avgInput = inputs.length ? inputs.reduce((a, b) => a + b, 0) / inputs.length : null;

  const kpis: { label: string; value: string; hint: string; tip?: TermKey }[] = [
    { label: "监控站点", value: String(siteIds.size), hint: "来自站点配置" },
    { label: "价格记录", value: String(records.length), hint: "自首次采集累计" },
    { label: "事件总数", value: String(events?.total ?? 0), hint: "新增与变化合计" },
    {
      label: "平均输入折扣",
      tip: "discount_input",
      value: avgInput !== null ? formatDiscount(avgInput) : "—",
      hint: "相对厂商价",
    },
  ];

  return (
    <>
      {error && <SiteAlert title="暂时读不到监控数据" detail={error} fix="稍后再试，或检查服务是否已启动。" />}
      <div className="dash-grid">
        <div className="dash-main">
          <div className="stat-grid">
            {kpis.map((card) => (
              <div key={card.label} className="stat-card">
                <div className="stat-label">
                  {card.label}
                  {card.tip && <TermTip term={card.tip} />}
                </div>
                <div className="stat-value">{card.value}</div>
                <div className="stat-hint">{card.hint}</div>
              </div>
            ))}
          </div>

          <div className="panel">
            <div className="dash-panel-head">
              <span className="dash-panel-title">最近事件</span>
              <Link href="/history" className="landing-more">
                全部事件 →
              </Link>
            </div>
            <div className="dash-events">
              {latestEvents.length > 0 ? (
                latestEvents.map((event, index) => {
                  const meta = eventMeta(event.kind);
                  const site = getSiteInfo(event.site_id, event.current?.source_url ?? event.previous?.source_url);
                  return (
                    <Link
                      key={`${event.site_id}:${event.model}:${event.detected_at}:${index}`}
                      href="/history"
                      className="event-card"
                    >
                      <span className={`side-dot dot-${meta.tone}`} style={{ marginTop: 5 }} />
                      <span style={{ minWidth: 0 }}>
                        <span style={{ display: "block", fontSize: 13.5, fontWeight: 550 }}>
                          {site.name} · {meta.label}
                        </span>
                        <span style={{ display: "block", fontSize: 12, color: "var(--text-3)", marginTop: 2 }}>
                          <span className="mono">{event.model}</span> · {formatTime(event.detected_at)}
                        </span>
                      </span>
                    </Link>
                  );
                })
              ) : (
                <p className="empty" style={{ padding: "28px 0" }}>
                  还没有事件，完成一轮采集后会显示在这里。
                </p>
              )}
            </div>
          </div>
        </div>

        <div className="dash-side">
          <div className="panel">
            <div className="dash-panel-head">
              <span className="dash-panel-title">快捷入口</span>
            </div>
            <div className="dash-quick">
              <Link href="/admin/tasks" className="dash-quick-item">
                <IconBolt size={15} /> 立即采集
              </Link>
              <Link href="/admin/sites" className="dash-quick-item">
                <IconPlus size={15} /> 新增站点
              </Link>
              <Link href="/admin/settings" className="dash-quick-item">
                <IconSettings size={15} /> 系统设置
              </Link>
              <Link href="/admin/docs" className="dash-quick-item">
                <IconBook size={15} /> 使用文档
              </Link>
            </div>
          </div>

          <div className="panel">
            <div className="dash-panel-head">
              <span className="dash-panel-title">公开页面</span>
            </div>
            <div className="dash-quick">
              <Link href="/overview" className="dash-quick-item">
                <IconMonitor size={15} /> 价格总览
              </Link>
              <Link href="/history" className="dash-quick-item">
                <IconSync size={15} /> 事件时间线
              </Link>
              <Link href="/catalog" className="dash-quick-item">
                <IconFileSearch size={15} /> 厂商定价
              </Link>
              <Link href="/discount" className="dash-quick-item">
                <IconAim size={15} /> 折扣明细
              </Link>
            </div>
          </div>
        </div>
      </div>

      {siteIds.size === 0 && !error && (
        <section className="guide section-gap">
          <h2>从这里开始</h2>
          <p className="guide-sub">三步完成第一次采集：</p>
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
                <div className="guide-title">2 · 配好 AI 提取与厂商定价</div>
                <p>标准格式的站点在本地直接算价，AI 负责识别模型别名和特殊格式；厂商价来自 models.dev 目录，折扣对比依赖它。</p>
              </div>
              <Link href="/admin/settings" className="guide-link">去设置 →</Link>
            </li>
            <li>
              <span className="guide-icon"><IconBolt size={15} /></span>
              <div>
                <div className="guide-title">3 · 触发首次采集</div>
                <p>先预览一轮确认解析结果，再勾选写入历史；之后每次采集都会自动积累事件与曲线。</p>
              </div>
              <Link href="/admin/tasks" className="guide-link">去采集 →</Link>
            </li>
          </ol>
          <p className="guide-foot">
            <IconAppstore size={13} /> 公开页面（总览 / 历史 / 厂商定价 / 折扣）无需登录即可浏览。
          </p>
        </section>
      )}
    </>
  );
}
