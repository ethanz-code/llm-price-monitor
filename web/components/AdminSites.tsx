"use client";

/** 站点管理：列表、启停、编辑与删除；数据在浏览器侧拉取管理员接口。 */

import { useCallback, useEffect, useState } from "react";
import { toast, Btn, Input, Modal, Sel, Switch } from "./ui";
import { DataTable, type DColumn } from "./DataTable";
import { apiSend } from "@/lib/api";
import { getSiteInfo } from "@/lib/sites";
import type { SiteConfig, SitesData } from "@/lib/types";

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
          <Input value={id} onChange={setId} placeholder="例如 example-newapi" style={{ width: 280, maxWidth: "100%" }} />
        </SettingRow>
        <SettingRow label="价格接口 URL">
          <Input value={url} onChange={setUrl} placeholder="https://example.com/api/pricing" style={{ width: 360, maxWidth: "100%" }} />
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
          <Input value={models} onChange={setModels} placeholder="gpt-5.6-sol, claude-5-sonnet" style={{ width: 360, maxWidth: "100%" }} />
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

export function AdminSites() {
  const [sites, setSites] = useState<SiteConfig[] | null>(null);
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

  return (
    <>
      <div className="admin-toolbar">
        <span className="admin-toolbar-hint">{sites?.length ?? 0} 个站点</span>
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
    </>
  );
}
