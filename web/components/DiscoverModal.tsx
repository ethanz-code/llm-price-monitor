"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { DataTable, type DColumn } from "./DataTable";
import { Btn, Check, Empty, Input, Modal, toast } from "./ui";
import { IconSearch } from "./icons";
import { DajuSit } from "./DajuArt";
import { apiSend, errorText } from "@/lib/api";
import { looseIncludes } from "@/lib/format";
import type { DiscoveryData, DiscoveryStation } from "@/lib/types";

type Tab = "pending" | "imported" | "all";

const TABS: { key: Tab; label: string }[] = [
  { key: "pending", label: "未监控" },
  { key: "imported", label: "已监控" },
  { key: "all", label: "全部" },
];

/** 站点简介：导航收录的描述，没有就显示 —。 */
function briefOf(row: DiscoveryStation): string {
  return row.description;
}

/** 毫秒数 → 展示值：≥1s 显示秒（一位小数），否则原样毫秒；缺失显示 —。 */
function msLabel(ms: number | null): string {
  if (ms == null || !Number.isFinite(ms)) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

/** 新站发现弹窗：从「导入站点」入口打开，发现/探测/导入全在弹窗里完成，不用再跑命令行脚本。
 *  导入只写基础配置（入口+停用态+new-api 公告口径），认证/倍率/网页模式等点「编辑」进站点弹窗补。
 *  以弹窗形态从「导入站点」入口打开，不常驻站点管理页。 */
export function DiscoverModal({
  open,
  onClose,
  onChanged,
  onEditSite,
}: {
  open: boolean;
  onClose: () => void;
  onChanged: () => void;
  onEditSite: (siteId: string) => void;
}) {
  const [data, setData] = useState<DiscoveryData | null>(null);
  const [keyword, setKeyword] = useState("");
  const [tab, setTab] = useState<Tab>("pending");
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [refreshing, setRefreshing] = useState(false);
  const [refreshProgress, setRefreshProgress] = useState("");
  const [importing, setImporting] = useState(false);
  const [confirmIgnore, setConfirmIgnore] = useState<DiscoveryStation | null>(null);
  const [ignoreReason, setIgnoreReason] = useState("");
  const [ignoring, setIgnoring] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await apiSend<DiscoveryData>("/api/discovery", "GET"));
    } catch {
      setData(null);
    }
  }, []);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const rows = useMemo<DiscoveryStation[]>(() => {
    let all = data?.stations ?? [];
    // 标记「忽略」的行原地保留：灰显禁用不消失，「全部」页签里沉底便于统一恢复
    if (tab === "pending") all = all.filter((row) => !row.imported_id);
    if (tab === "imported") all = all.filter((row) => row.imported_id);
    if (!keyword.trim()) return all;
    return all.filter(
      (row) => looseIncludes(row.host, keyword) || looseIncludes(row.name, keyword) || looseIncludes(briefOf(row), keyword),
    );
  }, [data, tab, keyword]);

  /** 标记/取消「忽略」（可附一句原因）；已监控的站标记时连带从站点管理删除（含历史数据），需显式确认。 */
  async function setIgnored(row: DiscoveryStation, ignored: boolean, alsoDelete: boolean, reason: string) {
    try {
      if (alsoDelete && row.imported_id) {
        await apiSend(`/api/sites/${encodeURIComponent(row.imported_id)}?purge=true`, "DELETE");
        onChanged();
      }
      await apiSend("/api/discovery/ignore", "POST", { hosts: [row.host], ignored, reason });
      toast(ignored ? (alsoDelete ? "已忽略，站点连同历史数据一并删除" : "已忽略，可在「全部」页签恢复") : "已取消标记");
      await load();
    } catch (error) {
      toast(`操作失败: ${errorText(error)}`);
    }
  }

  /** 后台探测任务快照：/api/tasks/{id} 的状态与日志，用来在弹窗里摆进度。 */
  type DiscoveryTask = { status: string; error?: string | null; logs?: { time: number; message: string; level: string }[] };

  /** 后台执行刷新（拉源 + 探测新候选）：整轮以十分钟计，轮询任务日志把进度摆出来，完成或失败立即收场。 */
  async function refresh() {
    setRefreshing(true);
    setRefreshProgress("已提交探测任务…");
    try {
      const before = data?.generated_at ?? "";
      const { task_id: taskId } = await apiSend<{ task_id: string }>("/api/discovery/refresh", "POST");
      for (let tries = 0; tries < 900; tries += 1) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        try {
          const task = await apiSend<DiscoveryTask>(`/api/tasks/${taskId}`, "GET");
          if (task.status === "done") {
            await load();
            toast("探测完成，列表已更新");
            return;
          }
          if (task.status === "failed") {
            toast(`探测失败：${task.error || "未知错误"}`);
            return;
          }
          const last = task.logs?.filter((log) => log.level === "info").at(-1)?.message ?? "";
          if (last && !last.startsWith("开始刷新")) setRefreshProgress(last);
        } catch {
          // 单次轮询失败继续等（服务重启会把任务标为失败，下一轮轮询能看到）
        }
        // 任务接口不可用时的兜底：数据时间变了也算完成
        try {
          const next = await apiSend<DiscoveryData>("/api/discovery", "GET");
          if (next.generated_at && next.generated_at !== before) {
            setData(next);
            toast("探测完成，列表已更新");
            return;
          }
        } catch {
          // 单次轮询失败继续等
        }
      }
      toast("还在后台探测中，稍后点一下刷新就能看到");
    } catch (error) {
      toast(`刷新失败: ${errorText(error)}`);
    } finally {
      setRefreshing(false);
      setRefreshProgress("");
    }
  }

  async function importSelected() {
    const hosts = [...selected];
    if (hosts.length === 0) return;
    setImporting(true);
    try {
      const result = await apiSend<{ imported: string[]; skipped: string[]; missing: string[] }>("/api/discovery/import", "POST", {
        hosts,
        enabled: false,
      });
      const parts = [`已导入 ${result.imported.length} 个（默认停用）`];
      if (result.skipped.length) parts.push(`跳过已有 ${result.skipped.length}`);
      if (result.missing.length) parts.push(`未找到 ${result.missing.length}`);
      toast(parts.join("，") + "；在下方站点列表点「编辑」补认证/倍率等配置");
      setSelected(new Set());
      await load();
      onChanged();
    } catch (error) {
      toast(`导入失败: ${errorText(error)}`);
    } finally {
      setImporting(false);
    }
  }

  const columns: DColumn<DiscoveryStation>[] = [
    {
      key: "select",
      title: "",
      width: 44,
      render: (_v, row) =>
        row.imported_id ? null : row.ignored ? (
          <Check disabled checked={false} onChange={() => {}}>
            {null}
          </Check>
        ) : (
          <Check
            checked={selected.has(row.host)}
            onChange={(next) =>
              setSelected((prev) => {
                const nextSet = new Set(prev);
                if (next) nextSet.add(row.host);
                else nextSet.delete(row.host);
                return nextSet;
              })
            }
          >
            {null}
          </Check>
        ),
    },
    {
      key: "host",
      title: "站点",
      // 大多数域名 ≤23 字符（mono 13px 约 180px）；更长的靠 ellipsis + title 兜底，宽度让给简介
      width: 208,
      ellipsis: true,
      render: (_v, row) => (
        <a
          href={row.url}
          target="_blank"
          rel="noreferrer"
          className="mono"
          title={row.ignored ? `${row.url}（已忽略）` : row.url}
          style={{ color: row.ignored ? "var(--text-3)" : "inherit", textDecorationColor: "var(--border-strong)" }}
        >
          {row.host}
        </a>
      ),
    },
    {
      key: "brief",
      title: "简介",
      width: 280,
      ellipsis: true,
      mobileHide: true,
      render: (_v, row) => {
        const brief = briefOf(row);
        const reason = row.ignored ? (row.ignore_reason || "").trim() : "";
        if (!brief && !reason) return <span style={{ color: "var(--text-3)" }}>—</span>;
        return (
          <div style={{ display: "grid", gap: 2 }}>
            {brief && (
              <span title={brief} style={{ color: row.ignored ? "var(--text-3)" : "var(--text-2)", fontSize: 12.5 }}>
                {brief}
              </span>
            )}
            {reason && (
              <span title={`忽略原因：${reason}`} style={{ color: "var(--text-3)", fontSize: 11.5 }}>
                忽略原因：{reason}
              </span>
            )}
          </div>
        );
      },
    },
    {
      key: "uptime",
      title: <span title="监测源自家探测的近 7 天在线比例">可用率 7 天</span>,
      width: 96,
      align: "right",
      render: (_v, row) =>
        row.uptime_7d != null ? (
          <span className="mono num" style={row.ignored ? { color: "var(--text-3)" } : undefined}>{row.uptime_7d}%</span>
        ) : (
          <span style={{ color: "var(--text-3)" }}>—</span>
        ),
    },
    {
      key: "avg",
      title: <span title="监测源自家探测的平均响应耗时">平均响应</span>,
      width: 84,
      align: "right",
      mobileHide: true,
      render: (_v, row) => (
        <span className="mono num" style={row.ignored ? { color: "var(--text-3)" } : undefined}>{msLabel(row.avg_ms)}</span>
      ),
    },
    {
      key: "last",
      title: <span title="监测源最近一次探测的响应耗时">最新响应</span>,
      width: 84,
      align: "right",
      mobileHide: true,
      render: (_v, row) => (
        <span
          className="mono num"
          title={row.checked_at ? `检查于 ${row.checked_at}` : undefined}
          style={row.ignored ? { color: "var(--text-3)" } : undefined}
        >
          {msLabel(row.last_ms)}
        </span>
      ),
    },
    {
      key: "state",
      title: "操作",
      width: 170,
      render: (_v, row) => {
        if (row.ignored) {
          return (
            <Btn variant="text" size="sm" onClick={() => void setIgnored(row, false, false, "")}>
              取消标记
            </Btn>
          );
        }
        if (row.imported_id) {
          return (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
              <span style={{ color: "var(--accent-text)" }}>已监控</span>
              <Btn variant="text" size="sm" onClick={() => onEditSite(row.imported_id!)}>
                编辑
              </Btn>
              <Btn
                variant="text"
                size="sm"
                onClick={() => {
                  setIgnoreReason(row.ignore_reason || "");
                  setConfirmIgnore(row);
                }}
                title="忽略该站，并从站点管理删除"
              >
                忽略
              </Btn>
            </span>
          );
        }
        return (
          <Btn
            variant="text"
            size="sm"
            onClick={() => {
              setIgnoreReason(row.ignore_reason || "");
              setConfirmIgnore(row);
            }}
          >
            忽略
          </Btn>
        );
      },
    },
  ];

  const pendingCount = data ? data.summary.total - data.summary.imported : 0;

  return (
    <Modal open={open} onClose={onClose} title="从新站发现导入" width={1120}>
      <div style={{ display: "grid", gap: 10 }}>
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10 }}>
          <div style={{ minWidth: 0, flex: "1 1 240px", fontSize: 12, color: "var(--text-3)" }}>
            {data
              ? `已收录 ${data.summary.total} · 已监控 ${data.summary.imported}${data.summary.ignored ? ` · 已忽略 ${data.summary.ignored}` : ""} · 数据时间 ${data.generated_at || "—"}（清单默认每日自动更新）`
              : "自动收录的中转站清单（站点与简介），勾选即可导入（默认停用）；清单默认每日自动更新"}
          </div>
          <Btn size="sm" loading={refreshing} onClick={refresh}>
            刷新发现
          </Btn>
        </div>
        {refreshing && (
          <div className="mono" style={{ fontSize: 12, color: "var(--text-3)" }} role="status">
            {refreshProgress || "后台探测中…"}
          </div>
        )}

      {data && (
        <>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
            <Input
              placeholder="搜索域名或简介"
              style={{ flex: "1 1 220px", maxWidth: 320 }}
              value={keyword}
              onChange={setKeyword}
              prefix={<IconSearch size={14} />}
            />
            <div role="group" aria-label="按监控状态筛选" style={{ display: "flex", gap: 6 }}>
              {TABS.map((item) => {
                const active = tab === item.key;
                return (
                  <button
                    key={item.key}
                    type="button"
                    aria-pressed={active}
                    onClick={() => {
                      setTab(item.key);
                      setSelected(new Set());
                    }}
                    style={{
                      border: "1px solid var(--border)",
                      borderRadius: 999,
                      padding: "4px 12px",
                      fontSize: 12.5,
                      cursor: "pointer",
                      background: active ? "var(--accent-text)" : "transparent",
                      color: active ? "var(--bg, #fff)" : "var(--text-2)",
                      borderColor: active ? "var(--accent-text)" : "var(--border)",
                    }}
                  >
                    {item.label}
                  </button>
                );
              })}
            </div>
          </div>

          {/* 表格区域限高内滚：弹窗高度稳定，底部「导入所选」操作条始终可见 */}
          <div
            style={{
              borderRadius: 8,
              overflow: "hidden",
              border: "1px solid var(--border)",
              maxHeight: "min(48vh, 430px)",
              overflowY: "auto",
            }}
          >
            <DataTable<DiscoveryStation>
              rowKey="host"
              columns={columns}
              rows={rows}
              scrollX={966}
              // 窄屏只剩 勾选44 + 站点208 + 可用率96 + 操作170 ≈ 518
              mobileScrollX={530}
              dense
              paginated
              defaultPageSize={20}
              empty={
                <Empty
                  icon={<DajuSit width={30} />}
                  title={tab === "pending" ? "没有待导入的新站" : "没有匹配的站点"}
                  description={tab === "pending" ? "点右上角「刷新发现」拉取最新站点清单。" : "换个筛选或关键词试试。"}
                />
              }
            />
          </div>

          <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10 }}>
            <span style={{ fontSize: 12.5, color: "var(--text-2)" }}>
              {selected.size > 0 ? `已选 ${selected.size} 个站点` : `待监控 ${pendingCount} 个；勾选后一键导入，默认停用、启用节奏由你控制`}
            </span>
            <span style={{ display: "inline-flex", gap: 8, marginLeft: "auto" }}>
              {selected.size > 0 && (
                <Btn size="sm" variant="text" onClick={() => setSelected(new Set())}>
                  清除选择
                </Btn>
              )}
              <Btn size="sm" variant="primary" loading={importing} disabled={selected.size === 0} onClick={importSelected}>
                导入所选
              </Btn>
            </span>
          </div>
        </>
      )}

      {/* 已监控站忽略 = 连带删除站点（含历史数据），破坏性操作单独确认 */}
      <Modal
        open={confirmIgnore !== null}
        onClose={() => setConfirmIgnore(null)}
        title={confirmIgnore?.imported_id ? "忽略并删除站点" : "忽略站点"}
        width={460}
      >
        {confirmIgnore && (
          <div style={{ display: "grid", gap: 14 }}>
            <p style={{ margin: 0, fontSize: 13.5, lineHeight: 1.7, color: "var(--text-2)" }}>
              将把 <span className="mono">{confirmIgnore.host}</span> 标记为「忽略」——以后不再出现在待导入清单里。
              {confirmIgnore.imported_id && (
                <>
                  同时从站点管理删除站点 <span className="mono">{confirmIgnore.imported_id}</span>，
                  <span style={{ color: "var(--text-2)" }}>含历史价格与事件数据，不可恢复。</span>
                </>
              )}
            </p>
            <label style={{ display: "grid", gap: 6, fontSize: 12.5, color: "var(--text-2)" }}>
              忽略原因（选填，会显示在灰显行上）
              <Input
                value={ignoreReason}
                onChange={setIgnoreReason}
                placeholder="例如：注册关闭 / 站方无数据 / 倍率拿不到（最长 200 字）"
              />
            </label>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <Btn size="sm" variant="text" onClick={() => setConfirmIgnore(null)}>
                取消
              </Btn>
              <Btn
                size="sm"
                variant="primary"
                loading={ignoring}
                onClick={async () => {
                  if (!confirmIgnore) return;
                  setIgnoring(true);
                  await setIgnored(confirmIgnore, true, Boolean(confirmIgnore.imported_id), ignoreReason);
                  setIgnoring(false);
                  setConfirmIgnore(null);
                }}
              >
                {confirmIgnore.imported_id ? "忽略并删除" : "忽略"}
              </Btn>
            </div>
          </div>
        )}
      </Modal>
      </div>
    </Modal>
  );
}
