"use client";

/** 站点导入导出：导出当前站点配置为 JSON 文件（含凭证）；导入走文件选择 + 冲突逐个确认，提交复用既有的增改接口。 */

import { useState } from "react";
import { Alert, Btn, Modal, Seg, toast } from "../ui";
import { apiSend, errorText } from "@/lib/api";
import type { SiteConfig } from "@/lib/types";

/** 导出文件的类型标记：导入时校验，防止把别的 JSON（如系统设置里的种子文件）喂进来 */
const EXPORT_KIND = "llm-price-monitor-sites";

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

/** 导出文件名带本地时间：站点配置-20261003-153045.json */
function exportFileName(): string {
  const now = new Date();
  const date = `${now.getFullYear()}${pad2(now.getMonth() + 1)}${pad2(now.getDate())}`;
  const time = `${pad2(now.getHours())}${pad2(now.getMinutes())}${pad2(now.getSeconds())}`;
  return `站点配置-${date}-${time}.json`;
}

/** 生成导出 JSON 并触发浏览器下载；sites 为空由调用方拦截 */
export function downloadSitesExport(sites: SiteConfig[]): void {
  const payload = { kind: EXPORT_KIND, version: 1, exported_at: new Date().toISOString(), sites };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = exportFileName();
  anchor.click();
  URL.revokeObjectURL(url);
}

/** 读入并校验导出文件，返回去重后的站点列表；格式不对抛出带原因的错误 */
export async function readSitesExportFile(file: File): Promise<SiteConfig[]> {
  let data: unknown;
  try {
    data = JSON.parse(await file.text());
  } catch {
    throw new Error("文件不是有效的 JSON");
  }
  if (!data || typeof data !== "object" || (data as { kind?: unknown }).kind !== EXPORT_KIND) {
    throw new Error("这不是站点配置导出文件（系统设置里的「种子导入」用的是另一套文件，两者不通用）");
  }
  const raw = (data as { sites?: unknown }).sites;
  if (!Array.isArray(raw)) throw new Error("文件里没有站点列表");
  const sites = new Map<string, SiteConfig>();
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const config = item as SiteConfig;
    const id = typeof config.id === "string" ? config.id.trim() : "";
    if (id) sites.set(id, { ...config, id }); // 文件里同 id 出现多次时以后面的为准
  }
  if (sites.size === 0) throw new Error("文件里没有可用站点");
  return [...sites.values()];
}

type ImportAction = "skip" | "overwrite";

/** 导入确认弹窗：新站点直接加入，与库里重名的逐个选跳过或覆盖（默认跳过）；确认后顺序提交，逐个统计结果。 */
export function ImportSitesModal({
  incoming,
  existingIds,
  onClose,
  onImported,
}: {
  incoming: SiteConfig[];
  existingIds: ReadonlySet<string>;
  onClose: () => void;
  onImported: () => void;
}) {
  // 重名站点的处理选择；缺省视为跳过（不碰库里已有的）
  const [actions, setActions] = useState<Record<string, ImportAction>>({});
  const [busy, setBusy] = useState(false);

  const fresh = incoming.filter((site) => !existingIds.has(site.id));
  const clashes = incoming.filter((site) => existingIds.has(site.id));
  const overwriteCount = clashes.filter((site) => actions[site.id] === "overwrite").length;
  const planCount = fresh.length + overwriteCount;

  async function runImport() {
    setBusy(true);
    let added = 0;
    let overwritten = 0;
    let skipped = 0;
    const failures: string[] = [];
    for (const site of incoming) {
      const action: ImportAction | "add" = existingIds.has(site.id) ? (actions[site.id] ?? "skip") : "add";
      if (action === "skip") {
        skipped += 1;
        continue;
      }
      try {
        if (action === "overwrite") {
          await apiSend(`/api/sites/${encodeURIComponent(site.id)}`, "PUT", { config: site });
          overwritten += 1;
        } else {
          await apiSend("/api/sites", "POST", { config: site });
          added += 1;
        }
      } catch (error) {
        failures.push(`${site.id}：${errorText(error)}`);
      }
    }
    onImported();
    const summary = `新增 ${added} 个、覆盖 ${overwritten} 个、跳过 ${skipped} 个`;
    if (failures.length) toast(`导入完成（${summary}），失败 ${failures.length} 个：${failures[0]}`);
    else toast(`导入完成：${summary}`);
  }

  return (
    <Modal
      open
      onClose={busy ? () => {} : onClose}
      title={`导入站点（${incoming.length}）`}
      width={520}
      footer={
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <Btn onClick={onClose} disabled={busy}>
            取消
          </Btn>
          <Btn variant="primary" onClick={runImport} loading={busy} disabled={planCount === 0}>
            {busy ? "导入中…" : planCount > 0 ? `导入 ${planCount} 个站点` : "没有要导入的站点"}
          </Btn>
        </div>
      }
    >
      <div style={{ display: "grid", gap: 10 }}>
        <p style={{ color: "var(--text-2)", margin: 0, fontSize: 13.5, lineHeight: 1.7 }}>
          文件里有 {incoming.length} 个站点：新增 {fresh.length} 个，
          {clashes.length > 0 ? `与现有站点重名 ${clashes.length} 个` : "没有重名站点"}。
          文件里没有提到的现有站点不受影响。
        </p>
        {clashes.length > 0 && (
          <Alert tone="warn">重名站点默认跳过；选「覆盖」会用文件里的配置整份替换现有配置，历史数据保留。</Alert>
        )}
        <div style={{ maxHeight: 300, overflowY: "auto", borderTop: "1px solid var(--border)" }}>
          {incoming.map((site) => {
            const clash = existingIds.has(site.id);
            return (
              <div
                key={site.id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 10,
                  padding: "8px 0",
                  borderBottom: "1px solid var(--border)",
                }}
              >
                <span
                  className="mono"
                  style={{ fontSize: 12.5, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                >
                  {site.id}
                </span>
                {clash ? (
                  <Seg
                    value={actions[site.id] ?? "skip"}
                    onChange={(value) => setActions((previous) => ({ ...previous, [site.id]: value === "overwrite" ? "overwrite" : "skip" }))}
                    options={[
                      { value: "skip", label: "跳过" },
                      { value: "overwrite", label: "覆盖" },
                    ]}
                  />
                ) : (
                  <span style={{ fontSize: 12, color: "var(--text-3)", flexShrink: 0 }}>新增</span>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </Modal>
  );
}
