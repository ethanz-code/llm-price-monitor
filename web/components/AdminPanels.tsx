"use client";

import { toast, Btn, Input, Progress, Sel, Switch } from "./ui";
import { DataTable, type DColumn } from "./DataTable";
import {
  IconAppstore,
  IconBolt,
  IconDashboard,
  IconHistory,
  IconSettings,
} from "./icons";
import { CollectButton } from "./CollectButton";
import { eventMeta, formatDiscount, formatTime } from "@/lib/format";
import { getSiteInfo } from "@/lib/sites";
import type { EventRow } from "@/lib/types";

export interface AdminSite {
  id: string;
  name: string;
  models: number;
  enabled: boolean;
}

export interface AdminKpis {
  sites: number;
  records: number;
  events: number;
  avgInput: number | null;
}

const RAIL = [
  { key: "admin-overview", icon: <IconDashboard size={15} />, label: "概览" },
  { key: "admin-sites", icon: <IconAppstore size={15} />, label: "站点管理" },
  { key: "admin-tasks", icon: <IconBolt size={15} />, label: "采集任务" },
  { key: "admin-events", icon: <IconHistory size={15} />, label: "事件审计" },
  { key: "admin-settings", icon: <IconSettings size={15} />, label: "采集设置" },
];

/** 示例任务行：任务列表 API 未上线前用于呈现面板形态。 */
const MOCK_TASKS = [
  { key: "t1", name: "全站点价格采集", trigger: "定时 · 每 6 小时", status: "done", cost: "42s", progress: 100 },
  { key: "t2", name: "官方价库刷新", trigger: "手动", status: "running", cost: "—", progress: 64 },
  { key: "t3", name: "全站点价格采集", trigger: "手动", status: "failed", cost: "18s", progress: 37 },
];

function StatusTag({ status }: { status: string }) {
  if (status === "done") return <span className="tag tone-green">完成</span>;
  if (status === "running") return <span className="tag tone-blue">运行中</span>;
  return <span className="tag tone-red">失败</span>;
}

export function AdminPanels({
  sites,
  kpis,
  events,
}: {
  sites: AdminSite[];
  kpis: AdminKpis;
  events: EventRow[];
}) {
  // 预览模式下的写操作统一给出行内提示
  function previewHint(feature: string) {
    toast(`${feature}为预览形态，将在登录体系上线后接入后端`);
  }

  const kpiCards = [
    { label: "监控站点", value: String(kpis.sites), hint: "来自采集配置" },
    { label: "价格记录", value: String(kpis.records), hint: "全部历史沉淀" },
    { label: "事件总数", value: String(kpis.events), hint: "新增与变化合计" },
    {
      label: "平均输入折扣",
      value: kpis.avgInput !== null ? formatDiscount(kpis.avgInput) : "—",
      hint: "相对厂商官方价",
    },
  ];

  const siteColumns: DColumn<AdminSite>[] = [
    {
      title: "站点",
      dataIndex: "name",
      render: (v: string) => (
        <span className="mono" style={{ fontWeight: 550 }}>{v}</span>
      ),
    },
    { title: "监控模型", dataIndex: "models", align: "right", width: 100 },
    {
      title: "采集状态",
      dataIndex: "enabled",
      width: 110,
      render: (v: boolean) => (
        <Switch defaultChecked={v} title={v ? "已启用" : "已停用"} onChange={() => previewHint("站点启停")} />
      ),
    },
    {
      title: "采集频率",
      key: "interval",
      width: 150,
      render: () => (
        <Sel
          defaultValue="6h"
          disabled
          style={{ width: 120 }}
          title="预览模式"
          options={[
            { value: "1h", label: "每 1 小时" },
            { value: "6h", label: "每 6 小时" },
            { value: "12h", label: "每 12 小时" },
          ]}
        />
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 120,
      render: () => (
        <span style={{ display: "inline-flex", gap: 4 }}>
          <Btn variant="text" size="sm" onClick={() => previewHint("站点编辑")}>
            编辑
          </Btn>
          <Btn variant="text" size="sm" disabled>
            删除
          </Btn>
        </span>
      ),
    },
  ];

  const taskColumns: DColumn<(typeof MOCK_TASKS)[number]>[] = [
    { title: "任务", dataIndex: "name" },
    { title: "触发方式", dataIndex: "trigger", width: 150 },
    { title: "状态", dataIndex: "status", width: 90, render: (v: string) => <StatusTag status={v} /> },
    {
      title: "进度",
      dataIndex: "progress",
      width: 180,
      render: (v: number) => <Progress percent={v} />,
    },
    { title: "耗时", dataIndex: "cost", width: 80, align: "right" },
  ];

  return (
    <div className="admin-shell">
      <aside className="admin-rail" aria-label="管理面板导航">
        <div className="admin-rail-title">Admin</div>
        {RAIL.map((item) => (
          <a key={item.key} href={`#${item.key}`}>
            {item.icon}
            {item.label}
          </a>
        ))}
      </aside>

      <div>
        <section id="admin-overview" className="admin-section">
          <div className="landing-section-head">
            <h2>概览</h2>
          </div>
          <div className="stat-grid">
            {kpiCards.map((card) => (
              <div key={card.label} className="stat-card">
                <div className="stat-label">{card.label}</div>
                <div className="stat-value">{card.value}</div>
                <div className="stat-hint">{card.hint}</div>
              </div>
            ))}
          </div>
        </section>

        <section id="admin-sites" className="admin-section">
          <div className="landing-section-head">
            <h2>站点管理</h2>
            <Btn size="sm" onClick={() => previewHint("新增站点")}>
              新增站点
            </Btn>
          </div>
          <div className="panel" style={{ overflow: "hidden" }}>
            <DataTable<AdminSite> rowKey="id" columns={siteColumns} rows={sites} />
          </div>
        </section>

        <section id="admin-tasks" className="admin-section">
          <div className="landing-section-head">
            <h2>采集任务</h2>
            <CollectButton size="sm" />
          </div>
          <div className="panel" style={{ overflow: "hidden" }}>
            <DataTable
              rowKey={(row) => row.key}
              columns={taskColumns}
              rows={MOCK_TASKS}
              footer={
                <div style={{ padding: "10px 16px", color: "var(--text-3)", fontSize: 12 }}>
                  任务列表为示例数据；点击"立即采集"会触发真实采集任务并在此跟踪。
                </div>
              }
            />
          </div>
        </section>

        <section id="admin-events" className="admin-section">
          <div className="landing-section-head">
            <h2>事件审计</h2>
          </div>
          <div className="panel" style={{ padding: "20px 24px" }}>
            {events.length > 0 ? (
              <ol className="tline">
                {events
                  .slice(-6)
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
              <span style={{ color: "var(--text-2)", fontSize: 13 }}>还没有事件记录。</span>
            )}
          </div>
        </section>

        <section id="admin-settings" className="admin-section">
          <div className="landing-section-head">
            <h2>采集设置</h2>
          </div>
          <div className="panel" style={{ padding: "20px 24px", display: "grid", gap: 16, maxWidth: 560 }}>
            <SettingRow label="采集频率">
              <Sel
                defaultValue="6h"
                style={{ width: 140 }}
                onChange={() => previewHint("采集频率")}
                options={[
                  { value: "1h", label: "每 1 小时" },
                  { value: "6h", label: "每 6 小时" },
                  { value: "12h", label: "每 12 小时" },
                  { value: "24h", label: "每 24 小时" },
                ]}
              />
            </SettingRow>
            <SettingRow label="自动写入历史与事件">
              <Switch defaultChecked onChange={() => previewHint("自动写入")} />
            </SettingRow>
            <SettingRow label="AI 兜底提取（格式不明站点）">
              <Switch defaultChecked onChange={() => previewHint("AI 兜底")} />
            </SettingRow>
            <SettingRow label="事件通知 Webhook">
              <Input placeholder="https://example.com/hook" style={{ width: 240 }} disabled />
            </SettingRow>
            <div style={{ display: "flex", gap: 10 }}>
              <Btn variant="primary" onClick={() => previewHint("保存设置")}>
                保存
              </Btn>
              <Btn onClick={() => previewHint("重置设置")}>重置</Btn>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

function SettingRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
      <span style={{ fontSize: 13.5 }}>{label}</span>
      {children}
    </div>
  );
}
