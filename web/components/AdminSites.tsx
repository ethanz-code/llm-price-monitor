"use client";

/** 站点管理：列表、启停、编辑与删除；数据在浏览器侧拉取管理员接口。 */

import { useCallback, useEffect, useRef, useState } from "react";
import { toast, Btn, Check, Empty, Switch } from "./ui";
import { DajuSit } from "./DajuArt";
import { DataTable, type DColumn } from "./DataTable";
import { apiSend } from "@/lib/api";
import { getSiteInfo } from "@/lib/sites";
import { RiskLink } from "./RiskLink";
import { SiteTestButton } from "./SiteTestButton";
import { ModelMultiSelect } from "./sites/MultiSelect";
import { errorText } from "@/lib/api";
import { SiteModal } from "./sites/SiteModal";
import { DeleteSitesModal, SiteHealthTip, SitesGuide } from "./sites/SitesExtras";
import type { SiteCollectHealth, SiteConfig, SitesData } from "@/lib/types";

/** 新建站点用的默认字段模板；认证等高级字段留空，需要时在高级配置 JSON 里补充。 */
function siteSkeleton(id = ""): SiteConfig {
  return {
    id,
    adapter: "standard",
    network: {
      url: null,
      params: {},
      headers: {},
    },
    auth_token: null,
    auth_header: "Authorization",
    auth_prefix: "Bearer ",
    cookie: null,
    cookies: {},
    request_headers: {},
    enabled: true,
  };
}

/** 列表标注用：取出 ratio_url 里的地址文本（两种形态都兼容） */
function ratioUrlText(row: SiteConfig): string {
  const ratio = row.network?.ratio_url;
  if (typeof ratio === "string") return ratio;
  return ratio !== null && typeof ratio === "object" && typeof ratio.url === "string" ? ratio.url : "";
}

export function AdminSites() {
  const [sites, setSites] = useState<SiteConfig[] | null>(null);
  const [siteHealth, setSiteHealth] = useState<Record<string, SiteCollectHealth>>({});
  const [editing, setEditing] = useState<{ config: SiteConfig; isNew: boolean } | null>(null);
  const [deleting, setDeleting] = useState<string[] | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [multiSelect, setMultiSelect] = useState(false);
  // 配置速查默认收起：不打扰熟手，新手需要时一眼能找到
  const [guideOpen, setGuideOpen] = useState(false);
  // 通用监控模型（settings.monitor_models）：全部站点共用一份，null 表示还没加载完
  const [monitorModels, setMonitorModels] = useState<string[] | null>(null);
  const [savedShown, setSavedShown] = useState(false);
  const savedTimer = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiSend<{ settings?: { monitor_models?: unknown } }>("/api/settings", "GET")
      .then((data) => {
        if (cancelled) return;
        const list = data.settings?.monitor_models;
        setMonitorModels(Array.isArray(list) ? list.filter((item): item is string => typeof item === "string") : []);
      })
      .catch(() => {
        if (!cancelled) setMonitorModels([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function saveMonitorModels(next: string[]) {
    // 保存成功才更新界面：失败时界面清单必须和库里保持一致，不能乐观更新后再不回滚
    try {
      await apiSend("/api/settings", "PUT", { settings: { monitor_models: next } });
      setMonitorModels(next);
      // 勾完即存没有保存按钮，用一闪而过的"已保存"确认落库
      setSavedShown(true);
      if (savedTimer.current !== null) window.clearTimeout(savedTimer.current);
      savedTimer.current = window.setTimeout(() => setSavedShown(false), 1600);
    } catch (error) {
      toast(`监控模型保存失败: ${errorText(error)}`);
    }
  }

  const reloadSites = useCallback(() => {
    apiSend<SitesData>("/api/sites", "GET")
      .then((data) => {
        setSites(data.sites);
        setSiteHealth(data.site_health ?? {});
        // 刷新后剔除已删除的站点；停用站点保留勾选（批量删除需要覆盖它们）
        setSelected((previous) => {
          const next = new Set<string>();
          for (const site of data.sites) {
            if (previous.has(site.id)) next.add(site.id);
          }
          return next;
        });
      })
      .catch(() => setSites([]));
  }, []);

  function toggleSelect(id: string, next: boolean) {
    setSelected((previous) => {
      const updated = new Set(previous);
      if (next) updated.add(id);
      else updated.delete(id);
      return updated;
    });
  }

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

  async function removeSites(ids: string[], purge: boolean) {
    try {
      await Promise.all(
        ids.map((id) => apiSend(`/api/sites/${encodeURIComponent(id)}?purge=${purge}`, "DELETE")),
      );
      toast(`已删除 ${ids.length} 个站点${purge ? "，相关历史数据一并清理" : ""}`);
      setDeleting(null);
      reloadSites();
    } catch (error) {
      toast(`删除失败: ${errorText(error)}`);
    }
  }

  const siteBaseColumns: DColumn<SiteConfig>[] = [
    {
      key: "id",
      title: "站点",
      // 站点名 + 公告/状态/倍率标注 + 报错图标的最小实宽，窄了标签会把名字挤换行
      width: 230,
      render: (_value, row) => {
        const site = getSiteInfo(row.id, typeof row.network?.url === "string" ? row.network.url : undefined);
        // 已开启的扩展接口小标注：公告/渠道状态/倍率，扫一眼就知道每个站点配了哪些采集
        const extraMarks: { label: string; tip: string }[] = [];
        if (typeof row.notice?.url === "string" && row.notice.url) extraMarks.push({ label: "公告", tip: `站点公告：${row.notice.url}` });
        if (typeof row.status?.url === "string" && row.status.url) extraMarks.push({ label: "状态", tip: `渠道状态：${row.status.url}` });
        if (typeof row.network?.ratio_url === "string" || (row.network?.ratio_url && typeof row.network.ratio_url === "object")) {
          const ratio = ratioUrlText(row);
          if (ratio) extraMarks.push({ label: "倍率", tip: `倍率接口：${ratio}` });
        }
        const nameNode = (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6, minWidth: 0 }}>
            <span className="mono" style={{ fontWeight: 550 }}>
              {site.name}
            </span>
            {extraMarks.length > 0 && (
              <span
                title={extraMarks.map((mark) => mark.tip).join("\n")}
                style={{ flexShrink: 0, fontSize: 11.5, color: "var(--text-3)", whiteSpace: "nowrap" }}
              >
                {extraMarks.map((mark) => mark.label).join("·")}
              </span>
            )}
            {/* 三类采集最近一次的异常：红=失败，黄=需认证/没抓到数据；停用站点不提示 */}
            {row.enabled && <SiteHealthTip health={siteHealth[row.id]} />}
          </span>
        );
        return site.homepage ? (
          <RiskLink href={site.homepage} variant="site">
            {nameNode}
          </RiskLink>
        ) : (
          nameNode
        );
      },
    },
    {
      key: "url",
      title: "接口地址",
      width: 240,
      mobileHide: true,
      ellipsis: true,
      render: (_value, row) => {
        const url = typeof row.network?.url === "string" ? row.network.url : "";
        return (
          <span className="mono" title={url || undefined} style={{ color: "var(--text-2)", fontSize: 12.5 }}>
            {url || "—"}
          </span>
        );
      },
    },
    {
      key: "enabled",
      title: "启用",
      width: 80,
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
      // 编辑 + 测试采集 两个按钮的实宽约 155px（fixed 布局下列宽不随内容扩展，窄了会向右溢出）
      width: 160,
      render: (_value, row) => (
        <span style={{ display: "inline-flex", gap: 4 }}>
          <Btn variant="text" size="sm" onClick={() => setEditing({ config: row, isNew: false })}>
            编辑
          </Btn>
          <SiteTestButton site={row} onDone={reloadSites} />
        </span>
      ),
    },
  ];

  const selectColumn: DColumn<SiteConfig> = {
    key: "select",
    title: "选择",
    width: 48,
    render: (_value, row) => (
      <Check checked={selected.has(row.id)} onChange={(next) => toggleSelect(row.id, next)}>
        {null}
      </Check>
    ),
  };

  // 勾选列只在批量管理模式下出现；停用中的站点也可勾选，供批量删除使用
  const siteColumns: DColumn<SiteConfig>[] = multiSelect ? [selectColumn, ...siteBaseColumns] : siteBaseColumns;

  return (
    <>
      {/* 配置速查：默认收起的一行说明条，展开后按场景给"怎么配" */}
      <div className="panel" style={{ padding: "10px 16px", marginBottom: 10 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
          <span style={{ fontSize: 13, color: "var(--text-2)" }}>各种情况怎么配？接口直采还是网页模式（Headless）、令牌过期怎么办，速查里都有</span>
          <Btn variant="text" size="sm" onClick={() => setGuideOpen((open) => !open)}>
            {guideOpen ? "收起速查" : "展开速查"}
          </Btn>
        </div>
        {guideOpen && <SitesGuide />}
      </div>
      <div className="panel" style={{ padding: "10px 16px", marginBottom: 10 }}>
        <div style={{ display: "grid", gridTemplateColumns: "minmax(120px, 200px) 1fr", gap: 12, alignItems: "start" }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 550 }}>监控模型</div>
            <div style={{ fontSize: 12, color: "var(--text-3)", marginTop: 2 }}>全部站点共用；列出要监控的模型名</div>
          </div>
          <div style={{ minWidth: 0 }}>
            {monitorModels === null ? (
              <span style={{ fontSize: 12.5, color: "var(--text-3)" }}>加载中…</span>
            ) : (
              <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <ModelMultiSelect value={monitorModels} onChange={saveMonitorModels} />
                {savedShown && (
                  <span style={{ fontSize: 12, color: "var(--accent)", whiteSpace: "nowrap" }}>已保存</span>
                )}
              </span>
            )}
          </div>
        </div>
      </div>
      <div
        className="panel"
        style={{ overflow: "hidden", borderBottom: "none", borderRadius: "8px 8px 0 0" }}
      >
        {sites === null ? (
          <div style={{ padding: "16px 20px", color: "var(--text-2)", fontSize: 13 }}>加载中…</div>
        ) : (
          <DataTable<SiteConfig>
            rowKey="id"
            columns={siteColumns}
            rows={sites}
            scrollX={860}
            mobileScrollX={560}
            empty={
              <Empty
                icon={<DajuSit width={30} />}
                title="还没有站点"
                description="添加第一个监控目标后，这里会展示各站点与模型的采集状态。"
                action={
                  <Btn size="sm" onClick={() => setEditing({ config: siteSkeleton(), isNew: true })}>
                    新增站点
                  </Btn>
                }
              />
            }
          />
        )}
      </div>

      {/* 表格底部操作条：与表格面板拼接，滚动时贴住屏幕底部始终可点 */}
      <div className="table-bottom-bar">
        <span style={{ display: "inline-flex", alignItems: "center", gap: 12 }}>
          {selected.size > 0 && (
            <>
              <span style={{ fontSize: 12.5, color: "var(--text-2)" }}>已选 {selected.size} 个站点</span>
              <Btn size="sm" onClick={() => setDeleting([...selected])}>
                删除所选
              </Btn>
              <Btn size="sm" variant="text" onClick={() => setSelected(new Set())}>
                清除选择
              </Btn>
            </>
          )}
        </span>
        <span style={{ display: "inline-flex", gap: 10 }}>
          <Btn
            size="sm"
            variant={multiSelect ? "primary" : "ghost"}
            title="显示勾选列，可批量删除站点"
            onClick={() =>
              setMultiSelect((open) => {
                if (open) setSelected(new Set());
                return !open;
              })
            }
          >
            批量管理
          </Btn>
          <Btn size="sm" variant="primary" onClick={() => setEditing({ config: siteSkeleton(), isNew: true })}>
            新增站点
          </Btn>
        </span>
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
        <DeleteSitesModal ids={deleting} onClose={() => setDeleting(null)} onDeleted={(purge) => removeSites(deleting, purge)} />
      )}
    </>
  );
}
