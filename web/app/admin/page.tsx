import Link from "next/link";
import { cookies } from "next/headers";
import { apiGet } from "@/lib/api";
import { eventMeta, formatDiscount, formatTime, isNoticeEvent } from "@/lib/format";
import { getSiteInfo } from "@/lib/sites";
import type { FeedData, OverviewData } from "@/lib/types";
import { SiteAlert } from "@/components/SiteAlert";
import { CollectErrorsCard } from "@/components/CollectErrorsCard";
import { TrafficPanel } from "@/components/TrafficPanel";
import {
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

// collect_status（需要关注的站点）仅管理员可见：与 overview 页同样带上会话 cookie
const SESSION_COOKIE = "ppm_session";

/** 概览：双栏仪表盘 —— 主栏顶部一行轻量统计数字 + 访问统计面板 + 最近事件，右栏快捷入口与公开页面；尚无站点时显示入门清单。 */
export default async function AdminOverviewPage() {
  const session = (await cookies()).get(SESSION_COOKIE)?.value;
  const headers = session ? { Cookie: `${SESSION_COOKIE}=${session}` } : undefined;
  let overview: OverviewData | null = null;
  let events: FeedData | null = null;
  let error: string | null = null;
  try {
    [overview, events] = await Promise.all([
      apiGet<OverviewData>("/api/overview", headers),
      // 事件总数给 KPI 用，events 列表给「最近事件」面板用
      apiGet<FeedData>("/api/feed?events_limit=5&notice_limit=5"),
    ]);
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }

  const records = overview?.records ?? [];
  const siteIds = new Set(records.map((row) => row.site_id));
  const latestEvents = events?.events.slice(0, 5) ?? [];
  const inputs = records.map((row) => row.discount?.input).filter((v): v is number => v !== null && v !== undefined);
  const avgInput = inputs.length ? inputs.reduce((a, b) => a + b, 0) / inputs.length : null;

  const kpis: { label: string; value: string; hint: string; tip?: TermKey }[] = [
    { label: "监控站点", value: String(siteIds.size), hint: "个" },
    { label: "价格记录", value: String(records.length), hint: "条" },
    { label: "事件总数", value: String((events?.price_total ?? 0) + (events?.notice_total ?? 0)), hint: "条" },
    {
      label: "平均输入折扣",
      tip: "discount_input",
      value: avgInput !== null ? formatDiscount(avgInput) : "—",
      hint: "相对厂商价",
    },
  ];

  return (
    <>
      {/* 后台各页均为面板式布局，页首标题只保留给读屏软件 */}
      <h1 className="sr-only">控制台</h1>
      {error && <SiteAlert title="暂时读不到监控数据" detail={error} fix="稍后再试，或检查服务是否已启动。" />}
      <div className="dash-grid">
        <div className="dash-main">
          <div className="dash-stats">
            {kpis.map((card) => (
              <div key={card.label} className="dash-stat">
                <div className="dash-stat-label">
                  {card.label}
                  {card.tip && <TermTip term={card.tip} />}
                </div>
                <div className="dash-stat-value">
                  {card.value}
                  <span className="dash-stat-hint">{card.hint}</span>
                </div>
              </div>
            ))}
          </div>

          <TrafficPanel />

          <CollectErrorsCard />

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
                  const site = getSiteInfo(event.site_id, isNoticeEvent(event) ? undefined : event.current?.source_url ?? event.previous?.source_url);
                  return (
                    <Link
                      key={`${event.site_id}:${event.kind}:${event.detected_at}:${index}`}
                      href="/history"
                      className="event-card"
                    >
                      <span className={`side-dot dot-${meta.tone}`} style={{ marginTop: 5 }} />
                      <span style={{ minWidth: 0 }}>
                        <span style={{ display: "block", fontSize: 13.5, fontWeight: 550 }}>
                          {site.name} · {meta.label}
                        </span>
                        <span style={{ display: "block", fontSize: 12, color: "var(--text-3)", marginTop: 2 }}>
                          {isNoticeEvent(event) ? "站点公告" : <span className="mono">{event.model}</span>} · {formatTime(event.detected_at)}
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
                <IconBolt size={15} /> 采集任务
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
                <p>标准格式的站点在本地直接算价，AI 负责识别模型别名和特殊格式；厂商价来自 models.dev 目录，总览里的折扣列依赖它。</p>
              </div>
              <Link href="/admin/settings" className="guide-link">去设置 →</Link>
            </li>
            <li>
              <span className="guide-icon"><IconBolt size={15} /></span>
              <div>
                <div className="guide-title">3 · 等待自动采集</div>
                <p>保存站点后，系统会按「系统设置」里的频率自动采集，事件与曲线随之积累；进度在采集任务页可见。</p>
              </div>
              <Link href="/admin/tasks" className="guide-link">看进度 →</Link>
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
