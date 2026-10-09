"use client";

/** 管理面板：概览 KPI、站点增删改、采集任务、事件审计与系统设置。
 *  站点/设置/任务数据在浏览器侧拉取管理员接口；未登录时浏览器原生弹出 Basic 登录框。 */

import { useCallback, useEffect, useState } from "react";
import { toast, Btn, Input, Modal, Sel, Switch } from "./ui";
import { DataTable, type DColumn } from "./DataTable";
import {
  IconAppstore,
  IconBolt,
  IconDashboard,
  IconHistory,
  IconSettings,
} from "./icons";
import { CollectButton } from "./CollectButton";
import { apiSend } from "@/lib/api";
import { eventMeta, formatDiscount, formatTime } from "@/lib/format";
import { getSiteInfo } from "@/lib/sites";
import type { EventRow, SettingsData, SiteConfig, SitesData, TaskInfo, TasksData } from "@/lib/types";

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
  { key: "admin-settings", icon: <IconSettings size={15} />, label: "系统设置" },
];

const TASK_KIND_LABELS: Record<string, string> = {
  collect: "全站价格采集",
  "official-refresh": "官方价库刷新",
};

const SITE_CREATE_TEMPLATE: SiteConfig = {
  id: "",
  adapter: "browser",
  network: { url: "", method: "GET" },
  models: [],
};

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function modelsToText(models: SiteConfig["models"]): string {
  return (models ?? []).map((item) => (typeof item === "string" ? item : item.name)).join(", ");
}

function StatusTag({ status }: { status: string }) {
  if (status === "done") return <span className="tag tone-green">完成</span>;
  if (status === "running") return <span className="tag tone-blue">运行中</span>;
  return <span className="tag tone-red">失败</span>;
}

function SettingRow({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "grid", gap: 4 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
        <span style={{ fontSize: 13.5 }}>{label}</span>
        {children}
      </div>
      {hint && <span style={{ fontSize: 12, color: "var(--text-3)" }}>{hint}</span>}
    </div>
  );
}

/* ---------- 站点编辑弹窗 ---------- */

function SiteModal({
  initial,
  isNew,
  onClose,
  onSaved,
}: {
  initial: SiteConfig;
  isNew: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const originalId = initial.id ?? "";
  const [id, setId] = useState(originalId);
  const [url, setUrl] = useState(typeof initial.network?.url === "string" ? initial.network.url : "");
  const [method, setMethod] = useState(typeof initial.network?.method === "string" ? initial.network.method : "GET");
  const [models, setModels] = useState(modelsToText(initial.models));
  const [enabled, setEnabled] = useState(initial.enabled !== false);
  const [advanced, setAdvanced] = useState(JSON.stringify(initial, null, 2));
  const [saving, setSaving] = useState(false);

  function buildConfig(): SiteConfig | null {
    let base: SiteConfig;
    try {
      base = JSON.parse(advanced) as SiteConfig;
    } catch {
      toast("高级配置不是合法 JSON");
      return null;
    }
    if (typeof base !== "object" || base === null || Array.isArray(base)) {
      toast("高级配置必须是 JSON 对象");
      return null;
    }
    const network = { ...(base.network ?? {}), url: url.trim() || null, method };
    return {
      ...base,
      id: id.trim(),
      network,
      models: models.split(/[,\n]/).map((item) => item.trim()).filter(Boolean),
      enabled,
    };
  }

  async function save() {
    const config = buildConfig();
    if (!config) return;
    setSaving(true);
    try {
      if (isNew) await apiSend("/api/sites", "POST", { config });
      else await apiSend(`/api/sites/${encodeURIComponent(originalId)}`, "PUT", { config });
      toast("站点已保存");
      onSaved();
      onClose();
    } catch (error) {
      toast(`保存失败: ${errorText(error)}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title={isNew ? "新增站点" : `编辑站点：${originalId}`}
      width={620}
      footer={
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <Btn onClick={onClose}>取消</Btn>
          <Btn variant="primary" loading={saving} onClick={save}>
            保存
          </Btn>
        </div>
      }
    >
      <div style={{ display: "grid", gap: 14 }}>
        <SettingRow label="站点 ID">
          <Input value={id} onChange={setId} placeholder="例如 example-newapi" style={{ width: 280 }} />
        </SettingRow>
        <SettingRow label="价格接口 URL">
          <Input value={url} onChange={setUrl} placeholder="https://example.com/api/pricing" style={{ width: 360 }} />
        </SettingRow>
        <SettingRow label="请求方法">
          <Sel
            value={method}
            onChange={setMethod}
            options={[
              { value: "GET", label: "GET" },
              { value: "POST", label: "POST" },
            ]}
          />
        </SettingRow>
        <SettingRow label="目标模型（逗号或换行分隔）" hint="需要分组的模型可在高级配置里写成 { name, group, aliases } 对象">
          <Input value={models} onChange={setModels} placeholder="gpt-5.6-sol, claude-5-sonnet" style={{ width: 360 }} />
        </SettingRow>
        <SettingRow label="启用采集">
          <Switch checked={enabled} onChange={setEnabled} />
        </SettingRow>
        <SettingRow
          label="高级配置 JSON"
          hint="完整站点配置（request_headers、network.headers/params/body、分组倍率等）；核心字段保存时以此覆盖"
        >
          <textarea
            className="input mono"
            value={advanced}
            onChange={(event) => setAdvanced(event.target.value)}
            rows={10}
            spellCheck={false}
            style={{ width: "100%", resize: "vertical", fontSize: 12, lineHeight: 1.6 }}
          />
        </SettingRow>
      </div>
    </Modal>
  );
}

/* ---------- 系统设置（AI / Tavily / Webhook） ---------- */

function SettingsSection() {
  const [data, setData] = useState<SettingsData | null>(null);
  const [tavilyKey, setTavilyKey] = useState("");
  const [webhook, setWebhook] = useState("");
  const [aiEnabled, setAiEnabled] = useState(true);
  const [aiBaseUrl, setAiBaseUrl] = useState("");
  const [aiModels, setAiModels] = useState("");
  const [aiApiKey, setAiApiKey] = useState("");
  const [aiTimeout, setAiTimeout] = useState("60");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    apiSend<SettingsData>("/api/settings", "GET")
      .then((loaded) => {
        setData(loaded);
        setTavilyKey(typeof loaded.settings.tavily_api_key === "string" ? loaded.settings.tavily_api_key : "");
        setWebhook(typeof loaded.settings.webhook === "string" ? loaded.settings.webhook : "");
        const ai = loaded.ai;
        setAiEnabled(ai.enabled !== false);
        setAiBaseUrl(typeof ai.base_url === "string" ? ai.base_url : "");
        setAiModels(modelsToText((ai.models as SiteConfig["models"]) ?? []));
        setAiApiKey(typeof ai.api_key === "string" ? ai.api_key : "");
        setAiTimeout(String(typeof ai.timeout === "number" ? ai.timeout : 60));
      })
      .catch(() => setData({ settings: {}, ai: {} }));
  }, []);

  async function save() {
    if (!data) return;
    const settings = { ...data.settings, tavily_api_key: tavilyKey.trim() || null, webhook: webhook.trim() || null };
    const ai: Record<string, unknown> = {
      ...data.ai,
      enabled: aiEnabled,
      base_url: aiBaseUrl.trim(),
      models: aiModels.split(/[,\n]/).map((item) => item.trim()).filter(Boolean),
      api_key: aiApiKey.trim() || null,
    };
    const timeout = Number.parseFloat(aiTimeout);
    if (Number.isFinite(timeout)) ai.timeout = timeout;
    setSaving(true);
    try {
      const saved = await apiSend<SettingsData>("/api/settings", "PUT", { settings, ai });
      setData(saved);
      toast("设置已保存，立即生效");
    } catch (error) {
      toast(`保存失败: ${errorText(error)}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section id="admin-settings" className="admin-section">
      <div className="landing-section-head">
        <h2>系统设置</h2>
      </div>
      <div className="panel" style={{ padding: "20px 24px", display: "grid", gap: 18, maxWidth: 620 }}>
        {!data ? (
          <span style={{ color: "var(--text-2)", fontSize: 13 }}>加载中…</span>
        ) : (
          <>
            <SettingRow label="AI 兜底提取" hint="格式不明站点交给 AI 从证据中提取价格（会产生 token 费用）">
              <Switch checked={aiEnabled} onChange={setAiEnabled} />
            </SettingRow>
            <SettingRow label="AI Base URL">
              <Input value={aiBaseUrl} onChange={setAiBaseUrl} placeholder="https://api.example.com/v1" style={{ width: 360 }} />
            </SettingRow>
            <SettingRow label="AI 模型列表" hint="逗号分隔；每次抽取随机选用一个">
              <Input value={aiModels} onChange={setAiModels} placeholder="model-a, model-b" style={{ width: 360 }} />
            </SettingRow>
            <SettingRow label="AI API Key" hint="保存在本机数据库中，不回传第三方">
              <Input value={aiApiKey} onChange={setAiApiKey} placeholder="sk-…" style={{ width: 360 }} />
            </SettingRow>
            <SettingRow label="AI 超时（秒）">
              <Input value={aiTimeout} onChange={setAiTimeout} style={{ width: 120 }} />
            </SettingRow>
            <SettingRow label="Tavily API Key" hint="官方价库刷新用；留空则回退 TAVILY_API_KEY 环境变量">
              <Input value={tavilyKey} onChange={setTavilyKey} placeholder="tvly-…" style={{ width: 360 }} />
            </SettingRow>
            <SettingRow label="事件通知 Webhook" hint="采集出现新增/变化时推送；留空则不推送">
              <Input value={webhook} onChange={setWebhook} placeholder="https://example.com/hook" style={{ width: 360 }} />
            </SettingRow>
            <div style={{ display: "flex", gap: 10 }}>
              <Btn variant="primary" loading={saving} onClick={save}>
                保存设置
              </Btn>
            </div>
          </>
        )}
      </div>
    </section>
  );
}

/* ---------- 主面板 ---------- */

export function AdminPanels({ kpis, events }: { kpis: AdminKpis; events: EventRow[] }) {
  const [sites, setSites] = useState<SiteConfig[] | null>(null);
  const [tasks, setTasks] = useState<TaskInfo[]>([]);
  const [editing, setEditing] = useState<{ config: SiteConfig; isNew: boolean } | null>(null);
  const [deleting, setDeleting] = useState<SiteConfig | null>(null);

  const reloadSites = useCallback(() => {
    apiSend<SitesData>("/api/sites", "GET")
      .then((data) => setSites(data.sites))
      .catch(() => setSites([]));
  }, []);

  useEffect(() => {
    reloadSites();
  }, [reloadSites]);

  useEffect(() => {
    let alive = true;
    const load = () => {
      apiSend<TasksData>("/api/tasks", "GET")
        .then((data) => {
          if (alive) setTasks(data.tasks);
        })
        .catch(() => {});
    };
    load();
    const timer = window.setInterval(load, 4000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  async function toggleSite(site: SiteConfig, next: boolean) {
    try {
      await apiSend(`/api/sites/${encodeURIComponent(site.id)}`, "PUT", { config: { ...site, enabled: next } });
      reloadSites();
    } catch (error) {
      toast(`更新失败: ${errorText(error)}`);
      reloadSites();
    }
  }

  async function removeSite(site: SiteConfig) {
    try {
      await apiSend(`/api/sites/${encodeURIComponent(site.id)}`, "DELETE");
      toast(`站点 ${site.id} 已删除`);
      setDeleting(null);
      reloadSites();
    } catch (error) {
      toast(`删除失败: ${errorText(error)}`);
    }
  }

  const kpiCards = [
    { label: "监控站点", value: String(kpis.sites), hint: "来自站点配置" },
    { label: "价格记录", value: String(kpis.records), hint: "自首次采集累计" },
    { label: "事件总数", value: String(kpis.events), hint: "新增与变化合计" },
    {
      label: "平均输入折扣",
      value: kpis.avgInput !== null ? formatDiscount(kpis.avgInput) : "—",
      hint: "相对厂商官方价",
    },
  ];

  const siteColumns: DColumn<SiteConfig>[] = [
    {
      key: "id",
      title: "站点",
      render: (_value, row) => (
        <span className="mono" style={{ fontWeight: 550 }}>
          {getSiteInfo(row.id, typeof row.network?.url === "string" ? row.network.url : undefined).name}
        </span>
      ),
    },
    {
      key: "url",
      title: "接口地址",
      render: (_value, row) => (
        <span className="mono" style={{ color: "var(--text-2)", fontSize: 12.5 }}>
          {typeof row.network?.url === "string" ? row.network.url : row.model_list_url || "—"}
        </span>
      ),
    },
    { key: "models", title: "模型数", align: "right", width: 90, render: (_v, row) => row.models?.length ?? 0 },
    {
      key: "enabled",
      title: "采集状态",
      width: 110,
      render: (_value, row) => (
        <Switch
          defaultChecked={row.enabled !== false}
          title={row.enabled !== false ? "已启用" : "已停用"}
          onChange={(next) => toggleSite(row, next)}
        />
      ),
    },
    {
      key: "actions",
      title: "操作",
      width: 130,
      render: (_value, row) => (
        <span style={{ display: "inline-flex", gap: 4 }}>
          <Btn variant="text" size="sm" onClick={() => setEditing({ config: row, isNew: false })}>
            编辑
          </Btn>
          <Btn variant="text" size="sm" onClick={() => setDeleting(row)}>
            删除
          </Btn>
        </span>
      ),
    },
  ];

  const taskColumns: DColumn<TaskInfo>[] = [
    { key: "kind", title: "任务", render: (_v, row) => TASK_KIND_LABELS[row.kind] ?? row.kind },
    { key: "status", title: "状态", width: 90, render: (_v, row) => <StatusTag status={row.status} /> },
    { key: "started_at", title: "开始时间", width: 150, render: (v: number) => formatTime(v) },
    {
      key: "cost",
      title: "耗时",
      width: 90,
      align: "right",
      render: (_v, row) =>
        row.finished_at ? `${Math.max(1, Math.round(row.finished_at - row.started_at))}s` : "—",
    },
    {
      key: "note",
      title: "备注",
      render: (_v, row) => {
        if (row.error) return <span style={{ color: "var(--tone-red-text)", fontSize: 12.5 }}>{row.error}</span>;
        if (row.status !== "done" || !row.result) return null;
        const result = row.result as { records?: unknown[]; models_found?: number };
        return (
          <span style={{ color: "var(--text-3)", fontSize: 12.5 }}>
            {row.kind === "official-refresh"
              ? `找到 ${String(result.models_found ?? 0)} 条`
              : `${(result.records ?? []).length} 条记录`}
          </span>
        );
      },
    },
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
            <Btn size="sm" onClick={() => setEditing({ config: SITE_CREATE_TEMPLATE, isNew: true })}>
              新增站点
            </Btn>
          </div>
          <div className="panel" style={{ overflow: "hidden" }}>
            {sites === null ? (
              <div style={{ padding: "16px 20px", color: "var(--text-2)", fontSize: 13 }}>加载中…</div>
            ) : (
              <DataTable<SiteConfig>
                rowKey="id"
                columns={siteColumns}
                rows={sites}
                empty="还没有站点；点击「新增站点」添加第一个监控目标。"
              />
            )}
          </div>
        </section>

        <section id="admin-tasks" className="admin-section">
          <div className="landing-section-head">
            <h2>采集任务</h2>
            <CollectButton size="sm" />
          </div>
          <div className="panel" style={{ overflow: "hidden" }}>
            <DataTable<TaskInfo>
              rowKey="id"
              columns={taskColumns}
              rows={tasks}
              empty="暂无任务记录；点击「立即采集」或「刷新官方价」后会在这里跟踪。"
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

        <SettingsSection />

        {editing && (
          <SiteModal
            initial={editing.config}
            isNew={editing.isNew}
            onClose={() => setEditing(null)}
            onSaved={reloadSites}
          />
        )}

        {deleting && (
          <Modal
            open
            onClose={() => setDeleting(null)}
            title="删除站点"
            width={420}
            footer={
              <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
                <Btn onClick={() => setDeleting(null)}>取消</Btn>
                <Btn variant="primary" onClick={() => removeSite(deleting)}>
                  确认删除
                </Btn>
              </div>
            }
          >
            <p style={{ color: "var(--text-2)", margin: 0, fontSize: 13.5, lineHeight: 1.7 }}>
              将删除站点 <span className="mono">{deleting.id}</span> 的采集配置；历史价格与事件数据保留在数据库中不受影响。
            </p>
          </Modal>
        )}
      </div>
    </div>
  );
}
