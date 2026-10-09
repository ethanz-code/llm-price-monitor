"use client";

/** 站点管理：列表、启停、编辑与删除；数据在浏览器侧拉取管理员接口。 */

import { useCallback, useEffect, useState } from "react";
import { toast, Btn, Check, Empty, Input, Modal, Switch } from "./ui";
import { IconAppstore, IconChevronDown } from "./icons";
import { DataTable, type DColumn } from "./DataTable";
import { apiSend } from "@/lib/api";
import { getSiteInfo } from "@/lib/sites";
import { RiskLink } from "./RiskLink";
import { formatTime, statusMeta } from "@/lib/format";
import { ToneTag } from "./ToneTag";
import { SiteTestButton } from "./SiteTestButton";
import type { SiteConfig, SitesData, SiteStatus, TaskInfo } from "@/lib/types";

/** 新建站点用的默认字段模板；认证等高级字段留空，需要时在高级配置 JSON 里补充。 */
function siteSkeleton(id = ""): SiteConfig {
  return {
    id,
    adapter: "standard",
    models: [],
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

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** 校验高级配置文本；合法 JSON 对象返回 null，否则返回错误说明。 */
function advancedJsonError(text: string): string | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    return error instanceof Error ? error.message : String(error);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return "高级配置要是一个 JSON 对象（最外层用 { } 包起来）";
  }
  return null;
}

function modelsToText(models: SiteConfig["models"]): string {
  return (models ?? []).filter((item): item is string => typeof item === "string").join(", ");
}

/** 目标模型：文本框逗号或换行分隔的纯名字列表。 */
function modelsFromParts(text: string): string[] {
  return text
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

/** 表单状态快照：保存与实时同步都基于它，保证两条路径语义一致。 */
type SiteFormState = {
  id: string;
  url: string;
  ratioUrl: string;
  statusUrl: string;
  noticeUrl: string;
  models: string;
  endpointHeadersText: string;
};

/** 键值对象 → "Key: Value" 多行文本。 */
function dictToText(dict: Record<string, string> | null | undefined): string {
  return Object.entries(dict ?? {})
    .map(([key, value]) => `${key}: ${value}`)
    .join("\n");
}

/** "Key: Value" 或 "Key=Value" 多行文本 → 键值对象；无法解析的行跳过。 */
function textToDict(text: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const match = /^\s*([^:=]+)[:=]\s*(.*)$/.exec(line);
    if (match) result[match[1].trim()] = match[2].trim();
  }
  return result;
}

/** 入口 URL（network.url）：表单留空时保留 JSON 里的对象形态；表单有值且 JSON 是对象时合并进对象的 url 字段。 */
function applyEntryUrl(network: Record<string, unknown>, key: string, text: string) {
  const trimmed = text.trim();
  const existing = network[key];
  if (!trimmed) {
    if (existing !== null && typeof existing === "object") return;
    network[key] = null;
    return;
  }
  if (existing !== null && typeof existing === "object" && !Array.isArray(existing)) {
    network[key] = { ...(existing as Record<string, unknown>), url: trimmed };
  } else {
    network[key] = trimmed;
  }
}

function networkFromForm(base: SiteConfig, form: SiteFormState): SiteConfig["network"] {
  const source =
    base.network !== null && typeof base.network === "object" && !Array.isArray(base.network)
      ? (base.network as Record<string, unknown>)
      : {};
  const network: Record<string, unknown> = { ...source };
  applyEntryUrl(network, "url", form.url);
  delete network.base_price_url;
  setOptionalText(network, "ratio_url", form.ratioUrl);
  setOptionalDict(network, "headers", form.endpointHeadersText);
  return network as SiteConfig["network"];
}

/** 文本可选项写回：非空写入（trim），空且原配置有该字段时移除，避免保存时塞默认噪音。 */
function setOptionalText(target: Record<string, unknown>, key: string, value: string, transform?: (v: string) => string) {
  const next = (transform ?? ((v: string) => v.trim()))(value);
  if (next) target[key] = next;
  else if (key in target) delete target[key];
}

/** 键值对可选项写回：有内容写入；清空时已有字段置空对象。 */
function setOptionalDict(target: Record<string, unknown>, key: string, text: string) {
  const parsed = textToDict(text);
  if (Object.keys(parsed).length > 0) target[key] = parsed;
  else if (key in target) target[key] = {};
}

/** 用表单覆盖基础字段（保存时使用）；认证等高级字段不经表单，仅在高级配置 JSON 里维护。 */
function applyForm(base: SiteConfig, form: SiteFormState): SiteConfig {
  const next: SiteConfig = {
    ...base,
    adapter: "standard",
    id: form.id.trim(),
    network: networkFromForm(base, form),
    models: modelsFromParts(form.models),
  };
  const statusUrl = form.statusUrl.trim();
  if (statusUrl) {
    next.status = {
      ...(typeof base.status === "object" && base.status !== null && !Array.isArray(base.status) ? base.status : {}),
      url: statusUrl,
    };
  } else if ("status" in next) delete next.status;
  const noticeUrl = form.noticeUrl.trim();
  if (noticeUrl) {
    next.notice = {
      ...(typeof base.notice === "object" && base.notice !== null && !Array.isArray(base.notice) ? base.notice : {}),
      url: noticeUrl,
    };
  } else if ("notice" in next) delete next.notice;
  return next;
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
  const [ratioUrl, setRatioUrl] = useState(typeof initial.network?.ratio_url === "string" ? initial.network.ratio_url : "");
  const [statusUrl, setStatusUrl] = useState(
    typeof (initial.status as { url?: unknown } | null | undefined)?.url === "string"
      ? ((initial.status as { url: string }).url)
      : "",
  );
  const [noticeUrl, setNoticeUrl] = useState(
    typeof (initial.notice as { url?: unknown } | null | undefined)?.url === "string"
      ? ((initial.notice as { url: string }).url)
      : "",
  );
  // 倍率/渠道状态/站点公告折叠区：编辑已有配置且任一 URL 已填时自动展开，避免用户以为值丢了
  const [extraOpen, setExtraOpen] = useState(
    Boolean(
      (typeof initial.network?.ratio_url === "string" && initial.network.ratio_url) ||
        (initial.status as { url?: unknown } | null | undefined)?.url ||
        (initial.notice as { url?: unknown } | null | undefined)?.url,
    ),
  );
  const [models, setModels] = useState(modelsToText(initial.models));
  const [endpointHeadersText, setEndpointHeadersText] = useState(dictToText(initial.network?.headers));
  const [jsonOpen, setJsonOpen] = useState(false);
  const [advanced, setAdvanced] = useState(JSON.stringify(initial, null, 2));
  const [saving, setSaving] = useState(false);
  const advancedError = advancedJsonError(advanced);

  function formState(): SiteFormState {
    return { id, url, ratioUrl, statusUrl, noticeUrl, models, endpointHeadersText };
  }

  // 表单字段变更实时合并进高级 JSON；JSON 非法时保留原文，不打断手动编辑
  function syncAdvanced(mutate: (base: SiteConfig) => SiteConfig) {
    setAdvanced((prev) => {
      let base: SiteConfig;
      try {
        base = JSON.parse(prev) as SiteConfig;
      } catch {
        return prev;
      }
      if (typeof base !== "object" || base === null || Array.isArray(base)) return prev;
      return JSON.stringify(mutate(base), null, 2);
    });
  }

  // 把高级 JSON 的核心字段回填到表单（手动编辑 JSON 或补全骨架后调用，保持两边一致）
  function configToForm(config: SiteConfig) {
    setId(typeof config.id === "string" ? config.id : "");
    setUrl(typeof config.network?.url === "string" ? config.network.url : "");
    const nextRatioUrl = typeof config.network?.ratio_url === "string" ? config.network.ratio_url : "";
    setRatioUrl(nextRatioUrl);
    const nextStatusUrl =
      typeof (config.status as { url?: unknown } | null | undefined)?.url === "string"
        ? (config.status as { url: string }).url
        : "";
    setStatusUrl(nextStatusUrl);
    if (nextRatioUrl || nextStatusUrl) setExtraOpen(true);
    setModels(modelsToText(config.models));
    setEndpointHeadersText(dictToText(config.network?.headers));
  }

  // 双向同步：手动编辑 JSON 且合法时，把核心字段回填到表单控件；JSON 未写完（非法）时只更新文本
  function onAdvancedChange(text: string) {
    setAdvanced(text);
    try {
      const parsed = JSON.parse(text) as SiteConfig;
      if (parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)) configToForm(parsed);
    } catch {
      // JSON 未写完时不打扰表单
    }
  }

  // 一键补全骨架：把缺失的字段以默认值合入高级 JSON，已填内容不动
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
    return applyForm(base, formState());
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
    <>
      <Modal
      open
      onClose={onClose}
      title={
        <span style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
          <span>{isNew ? "新增站点" : `编辑站点：${originalId}`}</span>
          <Btn
            variant="text"
            size="sm"
            title="高级配置 JSON：这里和表单会自动保持一致"
            onClick={() => setJsonOpen(true)}
          >
            <span className="mono" style={{ fontWeight: 600 }}>{"{ }"}</span>
          </Btn>
        </span>
      }
      width={620}
      footer={
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
          <Btn onClick={onClose}>取消</Btn>
          <Btn
            variant="primary"
            loading={saving}
            disabled={advancedError !== null}
            title={advancedError ? "高级配置 JSON 格式不对，改好才能保存" : undefined}
            onClick={save}
          >
            保存
          </Btn>
        </div>
      }
    >
      <div style={{ display: "grid", gap: 14, gridTemplateColumns: "minmax(0, 1fr)" }}>
        <SettingRow label="站点 ID">
          <Input
            value={id}
            onChange={(value) => {
              setId(value);
              syncAdvanced((base) => ({ ...base, id: value.trim() }));
            }}
            placeholder="例如 example-newapi"
            style={{ width: "min(280px, 100%)" }}
          />
        </SettingRow>
        <div style={{ display: "grid", gap: 12, minWidth: 0, padding: "2px 0" }}>
          <SettingRow label="价格接口 URL" hint="站点提供价格数据的接口地址；要带参数就直接拼在地址后面，如 ?page=1&lang=zh">
          <Input
            value={url}
            onChange={(value) => {
              setUrl(value);
              syncAdvanced((base) => ({ ...base, network: { ...(base.network ?? {}), url: value.trim() || null } }));
            }}
            placeholder="https://example.com/api/pricing"
            style={{ width: "min(360px, 100%)" }}
          />
        </SettingRow>
        <SettingRow label="目标模型（逗号或换行分隔）" hint="填模型名就行；站点上的别名不用手填，AI 会自动识别">
          <Input
            value={models}
            onChange={(value) => {
              setModels(value);
              syncAdvanced((base) => ({ ...base, models: modelsFromParts(value) }));
            }}
            placeholder="gpt-5.6-sol, claude-5-sonnet"
            style={{ width: "min(360px, 100%)" }}
          />
        </SettingRow>
          <SettingRow label="请求头（每行 Key: Value）" hint="一般不用填；站点要求特殊请求头时在这里加">
            <textarea
              className="input mono textarea"
              value={endpointHeadersText}
              onChange={(event) => {
                const value = event.target.value;
                setEndpointHeadersText(value);
                syncAdvanced((base) => {
                  const network = { ...(base.network ?? {}) } as Record<string, unknown>;
                  setOptionalDict(network, "headers", value);
                  return { ...base, network: network as SiteConfig["network"] };
                });
              }}
              rows={2}
              spellCheck={false}
              style={{ fontSize: 12 }}
            />
          </SettingRow>
          <div style={{ display: "flex", justifyContent: "center" }}>
            <Btn
              variant="text"
              size="sm"
              title={extraOpen ? "收起更多接口配置" : "展开更多接口配置"}
              ariaLabel={extraOpen ? "收起更多接口配置" : "展开更多接口配置"}
              onClick={() => setExtraOpen((open) => !open)}
            >
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                <span
                  style={{
                    display: "inline-flex",
                    transform: extraOpen ? "rotate(180deg)" : "none",
                    transition: "transform 150ms ease",
                  }}
                >
                  <IconChevronDown size={14} />
                </span>
                {extraOpen ? "收起更多接口配置" : "更多接口配置（倍率接口、渠道状态、站点公告）"}
              </span>
            </Btn>
          </div>
          {extraOpen && (
            <>
          <SettingRow label="倍率接口 URL" hint="填站点的倍率查询地址，采集时按倍率把厂商基准价换算成实售价；留空就直接用基准价">
            <Input
              value={ratioUrl}
              onChange={(value) => {
                setRatioUrl(value);
                syncAdvanced((base) => {
                  const network = { ...(base.network ?? {}) } as Record<string, unknown>;
                  setOptionalText(network, "ratio_url", value);
                  return { ...base, network: network as SiteConfig["network"] };
                });
              }}
              placeholder="https://example.com/api/public/model-pricing"
              style={{ width: "min(360px, 100%)" }}
            />
          </SettingRow>
          <SettingRow label="渠道状态 URL" hint="填站点的渠道状态接口地址，每次采集会顺带检查各渠道是否正常，有变化会记成事件">
            <Input
              value={statusUrl}
              onChange={(value) => {
                setStatusUrl(value);
                syncAdvanced((base) => {
                  const trimmed = value.trim();
                  const existing = typeof base.status === "object" && base.status !== null ? base.status : {};
                  if (!trimmed) {
                    const { status: _dropped, ...rest } = base;
                    return rest as SiteConfig;
                  }
                  return { ...base, status: { ...existing, url: trimmed } };
                });
              }}
              placeholder="https://example.com/api/status"
              style={{ width: "min(360px, 100%)" }}
            />
          </SettingRow>
          <SettingRow label="站点公告 URL" hint="留空会自动抓站点的 /api/notice（new-api/one-api 都是这个地址）；公告内容有变化时会记录并通知">
            <Input
              value={noticeUrl}
              onChange={(value) => {
                setNoticeUrl(value);
                syncAdvanced((base) => {
                  const trimmed = value.trim();
                  const existing = typeof base.notice === "object" && base.notice !== null ? base.notice : {};
                  if (!trimmed) {
                    const { notice: _dropped, ...rest } = base;
                    return rest as SiteConfig;
                  }
                  return { ...base, notice: { ...existing, url: trimmed } };
                });
              }}
              placeholder="https://example.com/api/notice"
              style={{ width: "min(360px, 100%)" }}
            />
          </SettingRow>
            </>
          )}
        </div>
        {advancedError && (
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <span style={{ fontSize: 12, color: "var(--tone-red-text)" }}>JSON 格式错误</span>
          </div>
        )}
      </div>
    </Modal>
      <Modal
        open={jsonOpen}
        onClose={() => setJsonOpen(false)}
        title="高级配置 JSON"
        width={780}
        footer={
          <Btn
            variant="text"
            size="sm"
            onClick={() => {
              try {
                setAdvanced(JSON.stringify(JSON.parse(advanced) as SiteConfig, null, 2));
              } catch {
                toast("JSON 格式不对，先改对再格式化");
              }
            }}
          >
            格式化
          </Btn>
        }
      >
        <div style={{ display: "grid", gap: 8 }}>
          {advancedError ? (
            <span style={{ fontSize: 12, color: "var(--tone-red-text)" }}>JSON 格式错误：{advancedError}</span>
          ) : (
            <span style={{ fontSize: 12, color: "var(--text-3)" }}>
              JSON 合法；这里和表单会自动保持一致。
            </span>
          )}
          <textarea
            className="input mono textarea"
            value={advanced}
            onChange={(event) => onAdvancedChange(event.target.value)}
            rows={22}
            spellCheck={false}
            style={{ fontSize: 12, minHeight: 0 }}
          />
        </div>
      </Modal>
    </>
  );
}

/** 批量采集所选站点：正式写入历史与事件，与「测试采集」的仅预览区分；任务进度用轮询跟踪。 */
function CollectSelectedModal({
  siteIds,
  onClose,
  onCollected,
}: {
  siteIds: string[];
  onClose: () => void;
  onCollected: () => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [task, setTask] = useState<TaskInfo | null>(null);
  const [elapsed, setElapsed] = useState<number | null>(null);

  async function poll(taskId: string, startedAt: number) {
    for (;;) {
      const res = await fetch(`/api/tasks/${taskId}`, { cache: "no-store" });
      const info = (await res.json()) as TaskInfo;
      setTask(info);
      if (info.status !== "running") {
        setElapsed(Math.max(1, Math.round((Date.now() - startedAt) / 1000)));
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
  }

  async function start() {
    setSubmitting(true);
    setTask(null);
    setElapsed(null);
    const startedAt = Date.now();
    try {
      const res = await fetch("/api/collect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ site_ids: siteIds, persist: true }),
      });
      if (res.status === 401) {
        window.location.href = "/login";
        throw new Error("需要管理员登录");
      }
      if (!res.ok) {
        const detail = ((await res.json()) as { detail?: string }).detail;
        throw new Error(detail ?? `HTTP ${res.status}`);
      }
      const { task_id: taskId } = (await res.json()) as { task_id: string };
      await poll(taskId, startedAt);
      onCollected();
    } catch (error) {
      toast(`采集失败: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setSubmitting(false);
    }
  }

  const running = task?.status === "running";
  const records = (task?.result?.records as unknown[] | undefined)?.length ?? 0;
  const events = (task?.result?.events as string[] | undefined)?.length ?? 0;
  const errors = (task?.result?.errors as { site_id: string; error: string }[] | undefined) ?? [];

  return (
    <Modal
      open
      onClose={() => {
        if (!running) onClose();
      }}
      title={`采集所选站点（${siteIds.length}）`}
    >
      <div style={{ display: "grid", gap: 14 }}>
        <p style={{ color: "var(--text-2)", margin: 0, fontSize: 13.5, lineHeight: 1.7 }}>
          将按已保存配置立即采集所选 {siteIds.length} 个启用中的站点，并写入历史与事件；结果可在总览与历史页查看。
        </p>
        {task && (
          <div style={{ display: "grid", gap: 6 }}>
            {task.status === "running" && <ToneTag tone="blue">采集中…</ToneTag>}
            {task.status === "done" && (
              <ToneTag tone="green">
                完成：{records} 条记录，{events} 个事件{elapsed !== null ? ` · 耗时 ${elapsed} 秒` : ""}
              </ToneTag>
            )}
            {task.status === "failed" && <ToneTag tone="red">失败：{task.error}</ToneTag>}
            {errors.map((item) => (
              <p key={item.site_id} style={{ color: "var(--tone-red-text)", fontSize: 13, margin: 0 }}>
                {item.site_id}: {item.error}
              </p>
            ))}
          </div>
        )}
        {!running && (
          <div>
            <Btn variant="primary" onClick={start} loading={submitting}>
              开始采集
            </Btn>
          </div>
        )}
      </div>
    </Modal>
  );
}

/** 删除站点确认弹窗：行内单删与勾选批删共用；勾选清理时后端同时删除该站点的历史与事件数据。 */
function DeleteSitesModal({
  ids,
  onClose,
  onDeleted,
}: {
  ids: string[];
  onClose: () => void;
  onDeleted: (purge: boolean) => void;
}) {
  const [purge, setPurge] = useState(false);
  const batch = ids.length > 1;
  return (
    <Modal
      open
      onClose={onClose}
      title={batch ? `删除所选站点（${ids.length}）` : "删除站点"}
      width={460}
      footer={
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <Btn onClick={onClose}>取消</Btn>
          <Btn variant="primary" onClick={() => onDeleted(purge)}>
            确认删除
          </Btn>
        </div>
      }
    >
      <div style={{ display: "grid", gap: 12 }}>
        <p style={{ color: "var(--text-2)", margin: 0, fontSize: 13.5, lineHeight: 1.7 }}>
          将删除{batch ? `所选 ${ids.length} 个站点` : <>站点 <span className="mono">{ids[0]}</span></>} 的采集配置；
          历史价格与事件数据默认保留在数据库中。
        </p>
        <Check checked={purge} onChange={setPurge}>
          同时清理{batch ? "这些站点" : "该站点"}的历史价格、事件与状态数据（不可恢复）
        </Check>
      </div>
    </Modal>
  );
}

export function AdminSites() {
  const [sites, setSites] = useState<SiteConfig[] | null>(null);
  const [collectStatus, setCollectStatus] = useState<Record<string, SiteStatus>>({});
  const [editing, setEditing] = useState<{ config: SiteConfig; isNew: boolean } | null>(null);
  const [deleting, setDeleting] = useState<string[] | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [multiSelect, setMultiSelect] = useState(false);
  const [batchOpen, setBatchOpen] = useState(false);

  const reloadSites = useCallback(() => {
    apiSend<SitesData>("/api/sites", "GET")
      .then((data) => {
        setSites(data.sites);
        setCollectStatus(data.collect_status ?? {});
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
      width: 140,
      render: (_value, row) => {
        const site = getSiteInfo(row.id, typeof row.network?.url === "string" ? row.network.url : undefined);
        return site.homepage ? (
          <RiskLink href={site.homepage} variant="site">
            <span className="mono" style={{ fontWeight: 550 }}>
              {site.name}
            </span>
          </RiskLink>
        ) : (
          <span className="mono" style={{ fontWeight: 550 }}>
            {site.name}
          </span>
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
    { key: "models", title: "模型数", align: "right", width: 90, render: (_v, row) => row.models?.length ?? 0 },
    {
      key: "collect",
      title: "最近采集",
      width: 110,
      mobileHide: true,
      render: (_v, row) => {
        const status = collectStatus[row.id];
        if (!status) return <span style={{ color: "var(--text-3)" }}>—</span>;
        const meta = statusMeta(row.enabled === false ? "disabled" : status.status);
        const reason = [status.error, status.checked_at ? formatTime(status.checked_at) : null].filter(Boolean).join(" · ");
        return (
          <span title={reason || undefined}>
            <ToneTag tone={meta.tone}>{meta.label}</ToneTag>
          </span>
        );
      },
    },
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
      // 编辑 + 测试采集 两个按钮的实宽约 155px（fixed 布局下列宽不随内容扩展，窄了会向右溢出）
      width: 160,
      render: (_value, row) => (
        <span style={{ display: "inline-flex", gap: 4 }}>
          <Btn variant="text" size="sm" onClick={() => setEditing({ config: row, isNew: false })}>
            编辑
          </Btn>
          <SiteTestButton site={row} />
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
      <div className="admin-toolbar">
        <span style={{ display: "inline-flex", alignItems: "center", gap: 12 }}>
          <span className="admin-toolbar-hint">{sites?.length ?? 0} 个站点</span>
          {selected.size > 0 && (
            <>
              <span style={{ fontSize: 12.5, color: "var(--text-2)" }}>已选 {selected.size} 个站点</span>
              <Btn size="sm" variant="primary" onClick={() => setBatchOpen(true)}>
                采集所选
              </Btn>
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
            title="显示勾选列，可批量采集或删除站点"
            onClick={() =>
              setMultiSelect((open) => {
                if (open) setSelected(new Set());
                return !open;
              })
            }
          >
            批量管理
          </Btn>
          <Btn size="sm" onClick={() => setEditing({ config: siteSkeleton(), isNew: true })}>
            新增站点
          </Btn>
        </span>
      </div>
      <div className="panel" style={{ overflow: "hidden" }}>
        {sites === null ? (
          <div style={{ padding: "16px 20px", color: "var(--text-2)", fontSize: 13 }}>加载中…</div>
        ) : (
          <DataTable<SiteConfig>
            rowKey="id"
            columns={siteColumns}
            rows={sites}
            scrollX={850}
            mobileScrollX={548}
            empty={
              <Empty
                icon={<IconAppstore size={18} />}
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

      {batchOpen && (
        <CollectSelectedModal siteIds={[...selected]} onClose={() => setBatchOpen(false)} onCollected={reloadSites} />
      )}
    </>
  );
}
