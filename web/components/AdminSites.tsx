"use client";

/** 站点管理：列表、启停、编辑与删除；数据在浏览器侧拉取管理员接口。 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { toast, Btn, Check, Empty, Input, Modal, Sel, Switch } from "./ui";
import { IconAppstore, IconCheck } from "./icons";
import { DataTable, type DColumn } from "./DataTable";
import { apiSend } from "@/lib/api";
import { getSiteInfo } from "@/lib/sites";
import { RiskLink } from "./RiskLink";
import { formatTime, looseIncludes, statusMeta } from "@/lib/format";
import { ToneTag } from "./ToneTag";
import { SiteTestButton } from "./SiteTestButton";
import type { CatalogData, SiteConfig, SitesData, SiteStatus } from "@/lib/types";

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

/** 表单状态快照：保存与实时同步都基于它，保证两条路径语义一致。 */
type SiteFormState = {
  id: string;
  url: string;
  statusUrl: string;
  statusGroupsText: string;
  noticeUrl: string;
  models: string[];
  endpointHeadersText: string;
};

/** 请求头键值行编辑共用的行结构 */
type KvRow = { key: string; value: string };

/** 键值文本 → 行数组（请求头行编辑的初值） */
function dictToRows(text: string): KvRow[] {
  return Object.entries(textToDict(text)).map(([key, value]) => ({ key, value }));
}

/** 行数组 → 键值文本（"Key: Value" 多行，空行名跳过） */
function rowsToText(rows: KvRow[]): string {
  return rows
    .filter((row) => row.key.trim())
    .map((row) => `${row.key.trim()}: ${row.value}`)
    .join("\n");
}

/** ratio_url 两种形态（纯字符串 / {url, headers} 对象）→ 表单用的地址与请求头文本 */
function ratioFromConfig(network: SiteConfig["network"]): { url: string; headersText: string } {
  const ratio = network?.ratio_url;
  if (typeof ratio === "string") return { url: ratio, headersText: "" };
  if (ratio !== null && typeof ratio === "object") {
    return { url: typeof ratio.url === "string" ? ratio.url : "", headersText: dictToText(ratio.headers) };
  }
  return { url: "", headersText: "" };
}

/** 列表标注用：取出 ratio_url 里的地址文本（两种形态都兼容） */
function ratioUrlText(row: SiteConfig): string {
  const ratio = row.network?.ratio_url;
  if (typeof ratio === "string") return ratio;
  return ratio !== null && typeof ratio === "object" && typeof ratio.url === "string" ? ratio.url : "";
}

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
  setOptionalDict(network, "headers", form.endpointHeadersText);
  return network as SiteConfig["network"];
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
    models: form.models,
  };
  const statusUrl = form.statusUrl.trim();
  if (statusUrl) {
    const existingStatus =
      typeof base.status === "object" && base.status !== null && !Array.isArray(base.status) ? base.status : {};
    // 检测分组：逗号分隔的分组名列表，留空清掉该字段（采集全量渠道）
    const groups = form.statusGroupsText
      .split(/[,，\n]/)
      .map((item) => item.trim())
      .filter(Boolean);
    next.status = {
      ...existingStatus,
      url: statusUrl,
      ...(groups.length > 0 ? { groups } : {}),
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

/** models.dev 目录条目 → 多选下拉的选项。 */
interface ModelOption {
  id: string;
  name: string;
  vendor: string;
}

/** 一次最多渲染的匹配项：目录数百条，全部渲染没必要。 */
const MODEL_MATCH_LIMIT = 60;

const MODEL_HINT_STYLE: React.CSSProperties = { fontSize: 12.5, color: "var(--text-3)", padding: "6px 8px" };

/** 目标模型多选：搜索勾选 models.dev 官方目录，目录外的名字输入后回车添加；已选项以标签展示、点 × 移除。 */
function ModelMultiSelect({ value, onChange }: { value: string[]; onChange: (next: string[]) => void }) {
  const [options, setOptions] = useState<ModelOption[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let cancelled = false;
    apiSend<CatalogData>("/api/catalog", "GET")
      .then((data) => {
        if (cancelled) return;
        setOptions(
          Object.values(data.models ?? {})
            .filter((entry) => typeof entry.model === "string" && entry.model)
            .map((entry) => ({ id: entry.model, name: entry.name ?? "", vendor: entry.vendor ?? "" }))
            .sort((a, b) => a.id.localeCompare(b.id)),
        );
      })
      .catch(() => {
        if (cancelled) return;
        setOptions([]);
        setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const selected = useMemo(() => new Set(value), [value]);
  const keyword = query.trim().toLowerCase();
  const matches = useMemo(() => {
    const source = options ?? [];
    if (!keyword) return source.slice(0, MODEL_MATCH_LIMIT);
    const hit = source.filter(
      (item) =>
        looseIncludes(item.id, keyword) ||
        looseIncludes(item.name, keyword) ||
        looseIncludes(item.vendor, keyword),
    );
    // 手动添加的名字不在目录里：把命中的已选项补进来，保证始终可见可移除
    for (const id of value) {
      if (looseIncludes(id, keyword) && !hit.some((item) => item.id === id)) {
        hit.unshift({ id, name: "", vendor: "" });
      }
    }
    return hit.slice(0, MODEL_MATCH_LIMIT);
  }, [options, keyword, value]);

  function toggle(id: string) {
    onChange(selected.has(id) ? value.filter((item) => item !== id) : [...value, id]);
  }

  function commitInput() {
    const id = query.trim();
    setQuery("");
    if (!id || selected.has(id)) return;
    onChange([...value, id]);
  }

  return (
    <span style={{ position: "relative", display: "inline-flex", width: "min(420px, 100%)" }}>
      <div
        role="button"
        aria-label="选择目标模型"
        onClick={() => setOpen(true)}
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 6,
          alignItems: "center",
          width: "100%",
          minHeight: 34,
          padding: "5px 10px",
          borderRadius: 6,
          border: "1px solid var(--border-strong)",
          background: "var(--panel)",
          cursor: "pointer",
          boxSizing: "border-box",
        }}
      >
        {value.length === 0 && (
          <span style={{ color: "var(--text-3)", fontSize: 13 }}>点开搜索勾选模型，自定义的也能加</span>
        )}
        {value.map((id) => (
          <span
            key={id}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 2,
              padding: "1px 2px 1px 8px",
              border: "1px solid var(--border)",
              borderRadius: 999,
              fontSize: 12,
              maxWidth: "100%",
            }}
          >
            <span className="mono" style={{ overflowWrap: "anywhere" }}>
              {id}
            </span>
            <span
              title="移除"
              role="button"
              aria-label={`移除 ${id}`}
              onClick={(event) => {
                event.stopPropagation();
                onChange(value.filter((item) => item !== id));
              }}
              style={{ cursor: "pointer", color: "var(--text-3)", padding: "2px 6px", borderRadius: 999 }}
            >
              ×
            </span>
          </span>
        ))}
      </div>
      {open && (
        <>
          <span style={{ position: "fixed", inset: 0, zIndex: 30 }} onClick={() => setOpen(false)} />
          <div
            style={{
              position: "absolute",
              top: "calc(100% + 4px)",
              left: 0,
              right: 0,
              zIndex: 31,
              background: "var(--panel)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              boxShadow: "0 12px 32px rgba(0, 0, 0, 0.18)",
              display: "grid",
            }}
          >
            <div style={{ padding: 8, borderBottom: "1px solid var(--border)" }}>
              <input
                className="input"
                autoFocus
                value={query}
                aria-label="搜索或添加模型"
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") commitInput();
                }}
                placeholder="搜模型名或厂商；回车把输入添加为自定义模型"
                style={{ height: 30, fontSize: 12.5 }}
              />
            </div>
            <div style={{ maxHeight: 240, overflowY: "auto", padding: 6, display: "grid", gap: 2 }}>
              {options === null && <span style={MODEL_HINT_STYLE}>模型目录加载中…</span>}
              {options !== null && options.length === 0 && (
                <span style={MODEL_HINT_STYLE}>
                  {failed ? "模型目录还没同步好，直接输入模型名回车添加" : "目录是空的，直接输入模型名回车添加"}
                </span>
              )}
              {options !== null && matches.length === 0 && (
                <span style={MODEL_HINT_STYLE}>没有匹配的模型；回车把当前输入添加为自定义模型</span>
              )}
              {matches.map((item) => {
                const picked = selected.has(item.id);
                return (
                  <div
                    key={item.id}
                    className="model-opt"
                    onClick={() => toggle(item.id)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      padding: "6px 8px",
                      borderRadius: 6,
                      cursor: "pointer",
                    }}
                  >
                    <span
                      aria-hidden
                      style={{
                        width: 16,
                        height: 16,
                        borderRadius: 4,
                        border: `1px solid ${picked ? "var(--accent)" : "var(--border-strong)"}`,
                        background: picked ? "var(--accent)" : "transparent",
                        color: picked ? "var(--accent-contrast)" : "transparent",
                        display: "grid",
                        placeItems: "center",
                        flexShrink: 0,
                      }}
                    >
                      <IconCheck size={11} />
                    </span>
                    <span className="mono" style={{ fontSize: 12.5, overflowWrap: "anywhere" }}>
                      {item.id}
                    </span>
                    {(item.name || item.vendor) && (
                      <span
                        style={{
                          marginLeft: "auto",
                          fontSize: 12,
                          color: "var(--text-3)",
                          whiteSpace: "nowrap",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                        }}
                      >
                        {[item.name, item.vendor].filter(Boolean).join(" · ")}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </>
      )}
    </span>
  );
}

function SettingRow({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "grid", gap: 4 }}>
      {/* label 包裹控件：读屏软件能把字段名和输入框关联起来 */}
      <label style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
        <span style={{ fontSize: 13.5 }}>{label}</span>
        {children}
      </label>
      {hint && <span style={{ fontSize: 12, color: "var(--text-3)" }}>{hint}</span>}
    </div>
  );
}

/* ---------- 站点编辑：子弹窗公共部分 ---------- */

/** 续签字段的局部覆盖：onChange 里刚敲入、还没写回主状态的值用 overrides 显式传入 */
type RefreshOverride = Partial<{
  method: string;
  url: string;
  refresh_token: string;
  body: string;
  response_sample: string;
  access_token_field: string;
  refresh_token_field: string;
}>;

/** 测试续签接口的成功返回：新 access_token 用于回填各处认证头，refresh_token 可能已被服务端换新 */
type RefreshTestResult = { access_token: string; refresh_token: string; refresh_token_rotated: boolean };

/** 认证与续签子弹窗提交回主弹窗的字段集合 */
type AuthFields = {
  method: string;
  url: string;
  token: string;
  body: string;
  sample: string;
  accessTokenField: string;
  refreshTokenField: string;
};

/** 价格采集子弹窗提交回主弹窗的字段集合 */
type PriceFields = {
  url: string;
  headersText: string;
  headless: HeadlessForm;
  ratioUrl: string;
  ratioHeadersText: string;
};

/** 网页模式·无头浏览器子弹窗的表单数据（localStorage 用行数组方便增删） */
type HeadlessForm = {
  enabled: boolean;
  cookies: { name: string; value: string }[];
  localStorage: { key: string; value: string }[];
  waitSeconds: string;
};

/** 站点配置 → 无头浏览器表单初值 */
function headlessFromConfig(config: SiteConfig): HeadlessForm {
  const headless = config.network?.headless;
  return {
    enabled: headless?.enabled === true,
    cookies: (Array.isArray(headless?.cookies) ? headless.cookies : [])
      .filter((item): item is { name: string; value: string } => typeof item?.name === "string")
      .map((item) => ({ name: item.name, value: typeof item.value === "string" ? item.value : "" })),
    localStorage: Object.entries(headless?.localStorage ?? {}).map(([key, value]) => ({
      key,
      value: typeof value === "string" ? value : "",
    })),
    waitSeconds: String(typeof headless?.wait_seconds === "number" ? headless.wait_seconds : 3),
  };
}

/** 测试续签成功后，把新 token 回填进整份配置：站点通用 token + 各接口 headers 里写死的 Authorization */
function applyRefreshResult(base: SiteConfig, result: RefreshTestResult): SiteConfig {
  const retokenHeaders = (headers: Record<string, string>): Record<string, string> =>
    Object.fromEntries(
      Object.entries(headers).map(([key, value]) => {
        if (key.toLowerCase() !== "authorization" || typeof value !== "string") return [key, value];
        const space = value.indexOf(" ");
        return [key, space > 0 ? `${value.slice(0, space)} ${result.access_token}` : result.access_token];
      }),
    );
  const hasAuth = (headers: Record<string, string> | undefined) =>
    !!headers && Object.keys(headers).some((key) => key.toLowerCase() === "authorization");
  return {
    ...base,
    auth_token: result.access_token,
    network:
      base.network && hasAuth(base.network.headers)
        ? { ...base.network, headers: retokenHeaders(base.network.headers!) }
        : base.network,
    networks: (base.networks ?? []).map((entry) =>
      hasAuth(entry.headers) ? { ...entry, headers: retokenHeaders(entry.headers!) } : entry,
    ),
    status:
      base.status && hasAuth(base.status.headers)
        ? { ...base.status, headers: retokenHeaders(base.status.headers!) }
        : base.status,
    notice:
      base.notice && hasAuth(base.notice.headers)
        ? { ...base.notice, headers: retokenHeaders(base.notice.headers!) }
        : base.notice,
    ...(base.token_refresh
      ? { token_refresh: { ...base.token_refresh, refresh_token: result.refresh_token } }
      : {}),
  };
}

/** 主弹窗中部的入口卡片：点击打开对应子弹窗；configured 时标注"已配置" */
function EntryCard({
  title,
  desc,
  configured,
  onClick,
}: {
  title: string;
  desc: string;
  configured: boolean;
  onClick: () => void;
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onClick();
        }
      }}
      style={{
        display: "grid",
        gap: 3,
        padding: "10px 12px",
        border: "1px solid var(--border)",
        borderRadius: 8,
        background: "var(--panel)",
        cursor: "pointer",
        transition: "border-color 150ms ease, background 150ms ease",
      }}
      onMouseEnter={(event) => {
        event.currentTarget.style.borderColor = "var(--border-strong)";
        event.currentTarget.style.background = "var(--panel-2)";
      }}
      onMouseLeave={(event) => {
        event.currentTarget.style.borderColor = "var(--border)";
        event.currentTarget.style.background = "var(--panel)";
      }}
    >
      <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 13.5, fontWeight: 550 }}>{title}</span>
        {configured && <span style={{ fontSize: 11, color: "var(--tone-green-text)" }}>已配置</span>}
      </span>
      <span style={{ fontSize: 12, color: "var(--text-3)" }}>{desc}</span>
    </div>
  );
}

/** 子弹窗底部按钮：取消丢弃本次修改，确定才写回主弹窗草稿 */
function SubModalFooter({
  onCancel,
  onConfirm,
  confirmDisabled,
  confirmTitle,
}: {
  onCancel: () => void;
  onConfirm: () => void;
  confirmDisabled?: boolean;
  confirmTitle?: string;
}) {
  return (
    <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
      <Btn onClick={onCancel}>取消</Btn>
      <Btn variant="primary" disabled={confirmDisabled} title={confirmTitle} onClick={onConfirm}>
        确定
      </Btn>
    </div>
  );
}

/* ---------- 子弹窗：请求头编辑区（各地址专用请求头共用） ---------- */

/** 单个地址的专用请求头编辑区：行编辑 + 覆盖提醒。warningKeys 为该地址 headers 里写死的认证头提示。 */
function HeadersEditor({
  rows,
  onChange,
  warningKeys,
}: {
  rows: KvRow[];
  onChange: (next: KvRow[]) => void;
  warningKeys: string[];
}) {
  return (
    <div style={{ display: "grid", gap: 6 }}>
      <span style={{ fontSize: 13.5 }}>请求头</span>
      <span style={{ fontSize: 12, color: "var(--text-3)" }}>
        这里的请求头只发给这个地址，同名会盖掉站点级认证头
      </span>
      {warningKeys.length > 0 && (
        <span style={{ fontSize: 12, color: "var(--tone-yellow-text)" }}>
          {warningKeys.join("、")} 里写死了认证头，它会优先于站点通用 Token 生效；续签拿到新 Token 后会自动把这里也换新，无需手动维护
        </span>
      )}
      <KeyValueRows
        rows={rows}
        onChange={onChange}
        keyLabel="名字"
        valueLabel="值"
        keyPlaceholder="名字，如 X-API-Key"
        valuePlaceholder="值"
        addLabel="添加请求头"
        ariaPrefix="请求头"
      />
    </div>
  );
}

/* ---------- 子弹窗：渠道状态 ---------- */

function StatusSubModal({
  initialUrl,
  initialGroups,
  initialHeadersText,
  warningKeys,
  onCommit,
  onClose,
}: {
  initialUrl: string;
  initialGroups: string;
  initialHeadersText: string;
  warningKeys: string[];
  onCommit: (url: string, groupsText: string, headersText: string) => void;
  onClose: () => void;
}) {
  const [url, setUrl] = useState(initialUrl);
  const [groupsText, setGroupsText] = useState(initialGroups);
  const [headerRows, setHeaderRows] = useState<KvRow[]>(() => dictToRows(initialHeadersText));
  return (
    <Modal
      open
      onClose={onClose}
      title="渠道状态"
      width={560}
      footer={
        <SubModalFooter
          onCancel={onClose}
          onConfirm={() => onCommit(url, groupsText, rowsToText(headerRows))}
        />
      }
    >
      <div style={{ display: "grid", gap: 14 }}>
        <SettingRow label="渠道状态 URL" hint="填站点的渠道状态接口地址，每次采集会顺带检查各渠道是否正常，有变化会记成事件">
          <Input
            value={url}
            onChange={setUrl}
            placeholder="https://example.com/api/status"
            style={{ width: "min(380px, 100%)" }}
          />
        </SettingRow>
        <SettingRow
          label="检测分组"
          hint="选填：只检测这些分组，从目标模型所在的分组里选，逗号分隔（中英文逗号都行），如 svip, vip；留空就检测全部渠道"
        >
          <Input
            value={groupsText}
            onChange={setGroupsText}
            placeholder="svip, vip"
            style={{ width: "min(380px, 100%)" }}
          />
          {groupsText.trim() && (
            <span style={{ fontSize: 12, color: "var(--tone-yellow-text)" }}>
              保存后，这个站点没被选中的分组的历史检测记录会被清掉，且无法恢复
            </span>
          )}
        </SettingRow>
        <HeadersEditor rows={headerRows} onChange={setHeaderRows} warningKeys={warningKeys} />
      </div>
    </Modal>
  );
}

/* ---------- 子弹窗：站点公告 ---------- */

function NoticeSubModal({
  initialUrl,
  initialHeadersText,
  warningKeys,
  onCommit,
  onClose,
}: {
  initialUrl: string;
  initialHeadersText: string;
  warningKeys: string[];
  onCommit: (url: string, headersText: string) => void;
  onClose: () => void;
}) {
  const [url, setUrl] = useState(initialUrl);
  const [headerRows, setHeaderRows] = useState<KvRow[]>(() => dictToRows(initialHeadersText));
  return (
    <Modal
      open
      onClose={onClose}
      title="站点公告"
      width={560}
      footer={<SubModalFooter onCancel={onClose} onConfirm={() => onCommit(url, rowsToText(headerRows))} />}
    >
      <div style={{ display: "grid", gap: 14 }}>
        <SettingRow
          label="站点公告 URL"
          hint="留空会自动抓站点的 /api/notice（new-api/one-api 都是这个地址）；公告内容有变化时会记录并通知。公告请求会自动带上价格接口的认证请求头，不用重复填"
        >
          <Input
            value={url}
            onChange={setUrl}
            placeholder="https://example.com/api/notice"
            style={{ width: "min(380px, 100%)" }}
          />
        </SettingRow>
        <HeadersEditor rows={headerRows} onChange={setHeaderRows} warningKeys={warningKeys} />
      </div>
    </Modal>
  );
}

/* ---------- 子弹窗：认证与续签 ---------- */

function AuthSubModal({
  initial,
  runTest,
  onCommit,
  onClose,
}: {
  initial: AuthFields;
  /** 真实调用续签接口；由主弹窗基于当前草稿组装完整配置发请求 */
  runTest: (overrides: RefreshOverride) => Promise<{ ok: boolean; text: string; result?: RefreshTestResult }>;
  onCommit: (fields: AuthFields, rotation?: RefreshTestResult) => void;
  onClose: () => void;
}) {
  const [method, setMethod] = useState(initial.method);
  const [url, setUrl] = useState(initial.url);
  const [token, setToken] = useState(initial.token);
  const [body, setBody] = useState(initial.body);
  const [sample, setSample] = useState(initial.sample);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);
  // 测试成功后暂存的新 token：点确定才随表单一并回写主弹窗草稿，取消则全部丢弃
  const [rotation, setRotation] = useState<RefreshTestResult | null>(null);

  // AI 分析出的字段路径没有表单入口，展示出来让用户知道分析到了什么
  const accessTokenField = initial.accessTokenField;
  const refreshTokenField = initial.refreshTokenField;

  // 续签即时校验：地址格式、请求体占位符、响应案例 JSON，填错当场提示不用等保存
  const urlError = url.trim() && !/^https?:\/\/\S+\.\S+/.test(url.trim()) ? "地址要以 http(s):// 开头且带域名" : "";
  const bodyError =
    body.trim() && !body.includes("${refresh_token}")
      ? "请求体里要写 ${refresh_token}，续签时才能自动代入凭证"
      : "";
  const sampleError = (() => {
    const text = sample.trim();
    if (!text) return "";
    try {
      JSON.parse(text);
      return "";
    } catch {
      return "案例不是合法 JSON，AI 分析不了；贴一段接口实际返回的 JSON";
    }
  })();

  const overrides = (): RefreshOverride => ({
    method,
    url,
    refresh_token: token,
    body,
    response_sample: sample,
  });
  const fields = (): AuthFields => ({
    method,
    url,
    token,
    body,
    sample,
    accessTokenField,
    refreshTokenField,
  });

  // 方法切到 POST 且请求体还空着时，自动填最常见的 JSON 模板，减少手写
  function onMethodChange(next: string) {
    setMethod(next);
    if (next !== "GET" && !body.trim()) setBody('{"refresh_token": "${refresh_token}"}');
    setTestResult(null);
  }

  function onUrlChange(next: string) {
    setUrl(next);
    // 首次填地址时自动补上标准请求体，手填过的不覆盖
    if (next.trim() && !body.trim()) setBody('{"refresh_token": "${refresh_token}"}');
    setTestResult(null);
  }

  // 真实调用一次续签接口：验证地址、凭证、响应结构是否都能对上
  async function test() {
    if (!url.trim()) {
      setTestResult({ ok: false, text: "先填续签接口地址再测试" });
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const outcome = await runTest(overrides());
      setTestResult({ ok: outcome.ok, text: outcome.text });
      if (outcome.ok && outcome.result) {
        // 新 refresh token 先回填到本弹窗输入框，点确定才落草稿
        setToken(outcome.result.refresh_token);
        setRotation(outcome.result);
      }
    } finally {
      setTesting(false);
    }
  }

  function commit() {
    onCommit(fields(), rotation ?? undefined);
  }

  return (
    <Modal open onClose={onClose} title="认证与续签" width={640} footer={<SubModalFooter onCancel={onClose} onConfirm={commit} />}>
      <div style={{ display: "grid", gap: 14 }}>
        <div style={{ display: "grid", gap: 8 }}>
          <span style={{ fontSize: 13.5 }}>Token 续签</span>
          <span style={{ fontSize: 12, color: "var(--text-3)" }}>
            站点认证 token 短效时，配一个续签接口：采集遇到"需认证"就自动调它换新 token 并重试
          </span>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <Sel
              value={method}
              onChange={onMethodChange}
              options={["GET", "POST", "PUT", "PATCH"].map((item) => ({ value: item, label: item }))}
              style={{ width: 92 }}
            />
            <Input
              value={url}
              onChange={onUrlChange}
              placeholder="续签接口地址，如 https://example.com/api/v1/auth/refresh"
              style={{ flex: 1, minWidth: 220 }}
            />
          </div>
          {urlError && <span style={{ fontSize: 12, color: "var(--tone-red-text)" }}>{urlError}</span>}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <Input
              value={token}
              onChange={(value) => {
                setToken(value);
                setTestResult(null);
              }}
              placeholder="Refresh Token，续签成功后会自动更新"
              style={{ flex: 1, minWidth: 220 }}
            />
            <Input
              value={body}
              onChange={(value) => {
                setBody(value);
                setTestResult(null);
              }}
              placeholder={'请求体，如 {"refresh_token": "${refresh_token}"}'}
              style={{ flex: 1, minWidth: 220 }}
            />
          </div>
          {bodyError && <span style={{ fontSize: 12, color: "var(--tone-red-text)" }}>{bodyError}</span>}
          <textarea
            className="input mono textarea"
            value={sample}
            onChange={(event) => setSample(event.target.value)}
            rows={3}
            spellCheck={false}
            placeholder="响应数据结构案例（选填）：贴一段续签接口实际返回的 JSON，保存时会自动分析出新 token 在哪"
            style={{ fontSize: 12 }}
          />
          {sampleError && <span style={{ fontSize: 12, color: "var(--tone-red-text)" }}>{sampleError}</span>}
          {(accessTokenField.trim() || refreshTokenField.trim()) && (
            <span style={{ fontSize: 12, color: "var(--text-3)" }}>
              已识别 token 位置：
              {[accessTokenField.trim() && `access_token = ${accessTokenField.trim()}`, refreshTokenField.trim() && `refresh_token = ${refreshTokenField.trim()}`]
                .filter(Boolean)
                .join(" · ")}
            </span>
          )}
          <span style={{ fontSize: 12, color: "var(--text-3)" }}>
            续签请求会自动带上上面的站点请求头；不贴案例时按常见返回结构自动找 token，贴了更准
          </span>
          <div>
            <Btn variant="text" size="sm" loading={testing} onClick={test}>
              测试续签
            </Btn>
          </div>
          {testResult && (
            <span style={{ fontSize: 12.5, color: testResult.ok ? "var(--tone-green-text)" : "var(--tone-red-text)" }}>
              {testResult.text}
            </span>
          )}
        </div>
      </div>
    </Modal>
  );
}

/* ---------- 子弹窗：网页模式·无头浏览器 ---------- */

/** 键值对编辑区：多行两列输入 + 删除/添加按钮；Cookie 和 localStorage 共用 */
function KeyValueRows({
  rows,
  onChange,
  keyLabel,
  valueLabel,
  keyPlaceholder,
  valuePlaceholder,
  addLabel,
  ariaPrefix,
}: {
  rows: { key: string; value: string }[];
  onChange: (next: { key: string; value: string }[]) => void;
  keyLabel: string;
  valueLabel: string;
  keyPlaceholder: string;
  valuePlaceholder: string;
  addLabel: string;
  ariaPrefix: string;
}) {
  return (
    <div style={{ display: "grid", gap: 6 }}>
      {rows.length === 0 && <span style={{ fontSize: 12, color: "var(--text-3)" }}>还没有添加，点下面加一条</span>}
      {rows.map((row, index) => (
        <div key={index} style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Input
            value={row.key}
            ariaLabel={`${ariaPrefix} ${keyLabel} ${index + 1}`}
            onChange={(next) => onChange(rows.map((item, i) => (i === index ? { ...item, key: next } : item)))}
            placeholder={keyPlaceholder}
            style={{ flex: 1, minWidth: 120 }}
          />
          <Input
            value={row.value}
            ariaLabel={`${ariaPrefix} ${valueLabel} ${index + 1}`}
            onChange={(next) => onChange(rows.map((item, i) => (i === index ? { ...item, value: next } : item)))}
            placeholder={valuePlaceholder}
            style={{ flex: 2, minWidth: 160 }}
          />
          <Btn
            variant="text"
            size="sm"
            ariaLabel={`删除第 ${index + 1} 条`}
            onClick={() => onChange(rows.filter((_, i) => i !== index))}
          >
            删除
          </Btn>
        </div>
      ))}
      <div>
        <Btn variant="text" size="sm" onClick={() => onChange([...rows, { key: "", value: "" }])}>
          {addLabel}
        </Btn>
      </div>
    </div>
  );
}

/** 无头浏览器配置区（受控）：价格采集子弹窗内使用，开关开启后展开 Cookie/localStorage/等待时间 */
function HeadlessSection({
  value,
  onChange,
}: {
  value: HeadlessForm;
  onChange: (next: HeadlessForm) => void;
}) {
  // Cookie 与 localStorage 统一用 key/value 行编辑，写回时再映射回 name/value
  const cookieRows = value.cookies.map((item) => ({ key: item.name, value: item.value }));
  return (
    <div style={{ display: "grid", gap: 14, borderTop: "1px solid var(--border)", paddingTop: 12 }}>
      <SettingRow label="用无头浏览器打开网页（注入登录信息）" hint="普通的接口采集不带登录状态；开启后改用无头浏览器打开页面，把下面的登录信息注进去">
        <Switch checked={value.enabled} onChange={(next) => onChange({ ...value, enabled: next })} />
      </SettingRow>
      {value.enabled && (
        <>
          <div style={{ display: "grid", gap: 6 }}>
            <span style={{ fontSize: 13.5 }}>Cookie</span>
            <span style={{ fontSize: 12, color: "var(--text-3)" }}>
              只填名字和值就行，域名、路径会按采集地址自动带上
            </span>
            <KeyValueRows
              rows={cookieRows}
              onChange={(rows) =>
                onChange({ ...value, cookies: rows.map((row) => ({ name: row.key, value: row.value })) })
              }
              keyLabel="名字"
              valueLabel="值"
              keyPlaceholder="名字，如 session"
              valuePlaceholder="值，如 abc123"
              addLabel="添加 Cookie"
              ariaPrefix="Cookie"
            />
          </div>
          <div style={{ display: "grid", gap: 6 }}>
            <span style={{ fontSize: 13.5 }}>localStorage</span>
            <KeyValueRows
              rows={value.localStorage}
              onChange={(rows) => onChange({ ...value, localStorage: rows })}
              keyLabel="键"
              valueLabel="值"
              keyPlaceholder="键，如 token"
              valuePlaceholder="值"
              addLabel="添加一项"
              ariaPrefix="localStorage"
            />
          </div>
          <SettingRow label="等待时间（秒）" hint="页面加载后等多久再读价格，页面慢就调大">
            <Input
              value={value.waitSeconds}
              type="number"
              ariaLabel="等待时间（秒）"
              onChange={(next) => onChange({ ...value, waitSeconds: next })}
              style={{ width: 100 }}
            />
          </SettingRow>
        </>
      )}
    </div>
  );
}

/* ---------- 子弹窗：价格采集（价格接口 + 无头浏览器 + 倍率接口） ---------- */

function PriceSubModal({
  initial,
  warningKeys,
  onCommit,
  onClose,
}: {
  initial: PriceFields;
  /** 价格接口与倍率接口 headers 里写死认证头的覆盖提醒 */
  warningKeys: string[];
  onCommit: (next: PriceFields) => void;
  onClose: () => void;
}) {
  const [url, setUrl] = useState(initial.url);
  const [headerRows, setHeaderRows] = useState<KvRow[]>(() => dictToRows(initial.headersText));
  const [headless, setHeadless] = useState(initial.headless);
  const [ratioUrl, setRatioUrl] = useState(initial.ratioUrl);
  const [ratioHeaderRows, setRatioHeaderRows] = useState<KvRow[]>(() => dictToRows(initial.ratioHeadersText));

  function commit() {
    // 等待时间收敛到 0~60；填空或非法回落默认 3
    const parsed = Number(headless.waitSeconds.trim());
    const waitSeconds = Number.isFinite(parsed) ? Math.min(60, Math.max(0, Math.round(parsed))) : 3;
    onCommit({
      url,
      headersText: rowsToText(headerRows),
      headless: {
        ...headless,
        cookies: headless.cookies.filter((item) => item.name.trim()),
        localStorage: headless.localStorage.filter((item) => item.key.trim()),
        waitSeconds: String(waitSeconds),
      },
      ratioUrl,
      ratioHeadersText: rowsToText(ratioHeaderRows),
    });
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="价格采集"
      width={640}
      footer={<SubModalFooter onCancel={onClose} onConfirm={commit} />}
    >
      <div style={{ display: "grid", gap: 14 }}>
        {/* 上块：价格接口 */}
        <div style={{ display: "grid", gap: 14 }}>
          <SettingRow
            label="价格接口 URL"
            hint="站点提供价格数据的接口地址；要带参数就直接拼在地址后面，如 ?page=1&lang=zh"
          >
            <Input
              value={url}
              onChange={setUrl}
              placeholder="https://example.com/api/pricing"
              style={{ width: "min(380px, 100%)" }}
            />
          </SettingRow>
          <HeadersEditor rows={headerRows} onChange={setHeaderRows} warningKeys={warningKeys} />
          <HeadlessSection value={headless} onChange={setHeadless} />
        </div>

        {/* 下块：倍率接口（公开接口，没有无头浏览器） */}
        <div style={{ display: "grid", gap: 14, borderTop: "1px solid var(--border)", paddingTop: 12 }}>
          <SettingRow
            label="倍率接口 URL"
            hint="填站点的倍率查询地址，采集时按倍率把厂商基准价换算成实售价；留空就直接用基准价"
          >
            <Input
              value={ratioUrl}
              onChange={setRatioUrl}
              placeholder="https://example.com/api/public/model-pricing"
              style={{ width: "min(380px, 100%)" }}
            />
          </SettingRow>
          <HeadersEditor rows={ratioHeaderRows} onChange={setRatioHeaderRows} warningKeys={[]} />
        </div>
      </div>
    </Modal>
  );
}

/* ---------- 子弹窗：高级 JSON ---------- */

function JsonSubModal({
  initial,
  onCommit,
  onClose,
}: {
  initial: string;
  onCommit: (text: string) => void;
  onClose: () => void;
}) {
  const [text, setText] = useState(initial);
  const error = advancedJsonError(text);
  return (
    <Modal
      open
      onClose={onClose}
      title="高级配置 JSON"
      width={780}
      footer={
        <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
          <Btn
            variant="text"
            size="sm"
            onClick={() => {
              try {
                setText(JSON.stringify(JSON.parse(text) as SiteConfig, null, 2));
              } catch {
                toast("JSON 格式不对，先改对再格式化");
              }
            }}
          >
            格式化
          </Btn>
          <SubModalFooter onCancel={onClose} onConfirm={() => onCommit(text)} confirmDisabled={error !== null} confirmTitle={error ? "高级配置 JSON 格式不对，改好才能确定" : undefined} />
        </div>
      }
    >
      <div style={{ display: "grid", gap: 8 }}>
        {error ? (
          <span style={{ fontSize: 12, color: "var(--tone-red-text)" }}>JSON 格式错误：{error}</span>
        ) : (
          <span style={{ fontSize: 12, color: "var(--text-3)" }}>JSON 合法；确定后会和主弹窗表单自动保持一致。</span>
        )}
        <textarea
          className="input mono textarea"
          value={text}
          onChange={(event) => setText(event.target.value)}
          rows={22}
          spellCheck={false}
          style={{ fontSize: 12, minHeight: 0 }}
        />
      </div>
    </Modal>
  );
}

/* ---------- 主弹窗 ---------- */

/** 子弹窗标识：主弹窗同一时间最多打开一个 */
type SubKey = "price" | "auth" | "status" | "notice" | "json";

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
  const [ratioUrl, setRatioUrl] = useState(() => ratioFromConfig(initial.network).url);
  const [ratioHeadersText, setRatioHeadersText] = useState(() => ratioFromConfig(initial.network).headersText);
  const [statusUrl, setStatusUrl] = useState(
    typeof (initial.status as { url?: unknown } | null | undefined)?.url === "string"
      ? ((initial.status as { url: string }).url)
      : "",
  );
  const [statusGroupsText, setStatusGroupsText] = useState(() => {
    const groups = (initial.status as { groups?: unknown } | null | undefined)?.groups;
    return Array.isArray(groups) ? groups.filter((item): item is string => typeof item === "string").join(", ") : "";
  });
  const [statusHeadersText, setStatusHeadersText] = useState(() =>
    dictToText((initial.status as { headers?: Record<string, string> } | null | undefined)?.headers),
  );
  const [noticeUrl, setNoticeUrl] = useState(
    typeof (initial.notice as { url?: unknown } | null | undefined)?.url === "string"
      ? ((initial.notice as { url: string }).url)
      : "",
  );
  const [noticeHeadersText, setNoticeHeadersText] = useState(() =>
    dictToText((initial.notice as { headers?: Record<string, string> } | null | undefined)?.headers),
  );
  const [models, setModels] = useState<string[]>(
    (initial.models ?? []).filter((item): item is string => typeof item === "string"),
  );
  const [endpointHeadersText, setEndpointHeadersText] = useState(dictToText(initial.network?.headers));
  // Token 续签：站点认证 token 短效时，配置一个续签接口，采集遇到"需认证"就自动换新 token 并重试
  const [refreshMethod, setRefreshMethod] = useState(
    typeof initial.token_refresh?.method === "string" ? initial.token_refresh.method.toUpperCase() : "POST",
  );
  const [refreshUrl, setRefreshUrl] = useState(typeof initial.token_refresh?.url === "string" ? initial.token_refresh.url : "");
  const [refreshToken, setRefreshToken] = useState(
    typeof initial.token_refresh?.refresh_token === "string" ? initial.token_refresh.refresh_token : "",
  );
  const [refreshBody, setRefreshBody] = useState(typeof initial.token_refresh?.body === "string" ? initial.token_refresh.body : "");
  const [refreshSample, setRefreshSample] = useState(
    typeof initial.token_refresh?.response_sample === "string" ? initial.token_refresh.response_sample : "",
  );
  // AI 分析出的字段路径没有表单入口，编辑时原样保留，避免一次无关修改把它抹掉
  const [accessTokenField, setAccessTokenField] = useState(
    typeof initial.token_refresh?.access_token_field === "string" ? initial.token_refresh.access_token_field : "",
  );
  const [refreshTokenField, setRefreshTokenField] = useState(
    typeof initial.token_refresh?.refresh_token_field === "string" ? initial.token_refresh.refresh_token_field : "",
  );
  // 网页模式·无头浏览器表单草稿
  const [headless, setHeadless] = useState<HeadlessForm>(() => headlessFromConfig(initial));
  const [activeSub, setActiveSub] = useState<SubKey | null>(null);
  const [advanced, setAdvanced] = useState(JSON.stringify(initial, null, 2));
  const [saving, setSaving] = useState(false);
  const advancedError = advancedJsonError(advanced);

  // 续签即时校验：地址格式、请求体 JSON、响应案例 JSON，填错当场提示不用等保存
  const refreshUrlError = refreshUrl.trim() && !/^https?:\/\/\S+\.\S+/.test(refreshUrl.trim()) ? "地址要以 http(s):// 开头且带域名" : "";
  const refreshBodyError =
    refreshBody.trim() && !refreshBody.includes("${refresh_token}")
      ? "请求体里要写 ${refresh_token}，续签时才能自动代入凭证"
      : "";
  const refreshSampleError = (() => {
    const sample = refreshSample.trim();
    if (!sample) return "";
    try {
      JSON.parse(sample);
      return "";
    } catch {
      return "案例不是合法 JSON，AI 分析不了；贴一段接口实际返回的 JSON";
    }
  })();

  // 认证凭证（auth_token/Cookie）是站点级通用的，价格、渠道状态、公告采集都会自动带上；
  // 哪个接口的 headers 里手写了 Authorization/Cookie 就会盖掉通用凭证（续签换新后旧的会失效），
  // 这里按地址分组检测，提醒展示在各自子弹窗的请求头编辑区
  const headerOverrideWarnings = (() => {
    let base: SiteConfig;
    try {
      base = JSON.parse(advanced) as SiteConfig;
    } catch {
      return { price: [] as string[], status: [] as string[], notice: [] as string[] };
    }
    const pick = (headers: Record<string, unknown>) =>
      Object.keys(headers).filter((key) => /^(authorization|cookie)$/i.test(key));
    const sectionKeys = (section: "network" | "status" | "notice") =>
      pick((base[section] as { headers?: Record<string, unknown> } | null)?.headers ?? {}).map(
        (key) => `${section}.headers.${key}`,
      );
    // 倍率接口支持 {url, headers} 对象形态，headers 里写死认证头同样会覆盖
    const ratio = base.network?.ratio_url;
    const ratioKeys =
      ratio !== null && typeof ratio === "object"
        ? pick((ratio as { headers?: Record<string, unknown> }).headers ?? {}).map((key) => `ratio_url.headers.${key}`)
        : [];
    return {
      price: [...sectionKeys("network"), ...ratioKeys],
      status: sectionKeys("status"),
      notice: sectionKeys("notice"),
    };
  })();

  // 续签配置 → token_refresh 字段；续签 URL 清空视为整体移除。
  // overrides：子弹窗里刚敲入的值还没进 React 状态，必须显式传入，否则会同步成旧值
  function tokenRefreshConfig(overrides: RefreshOverride = {}): SiteConfig["token_refresh"] | null {
    const targetUrl = (overrides.url ?? refreshUrl).trim();
    if (!targetUrl) return null;
    const body = (overrides.body ?? refreshBody).trim();
    const atField = (overrides.access_token_field ?? accessTokenField).trim();
    const rtField = (overrides.refresh_token_field ?? refreshTokenField).trim();
    const sample = (overrides.response_sample ?? refreshSample).trim();
    return {
      url: targetUrl,
      method: overrides.method ?? refreshMethod,
      refresh_token: (overrides.refresh_token ?? refreshToken).trim(),
      ...(body ? { body } : {}),
      ...(sample ? { response_sample: sample } : {}),
      ...(atField ? { access_token_field: atField } : {}),
      ...(rtField ? { refresh_token_field: rtField } : {}),
    };
  }

  function syncTokenRefresh(overrides: RefreshOverride = {}) {
    syncAdvanced((base) => {
      const next = tokenRefreshConfig(overrides);
      if (next === null) {
        // 清空地址多半是在改地址，此刻删整个 token_refresh 会连带丢掉 refresh_token/请求体；
        // 先不动 JSON，"地址为空 = 移除续签"留到保存时统一处理
        return base;
      }
      const merged = base.token_refresh ? { ...base.token_refresh, ...next } : next;
      return { ...base, token_refresh: merged };
    });
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
    const ratio = ratioFromConfig(config.network);
    setRatioUrl(ratio.url);
    setRatioHeadersText(ratio.headersText);
    const nextStatusUrl =
      typeof (config.status as { url?: unknown } | null | undefined)?.url === "string"
        ? (config.status as { url: string }).url
        : "";
    setStatusUrl(nextStatusUrl);
    const nextGroups = (config.status as { groups?: unknown } | null | undefined)?.groups;
    setStatusGroupsText(
      Array.isArray(nextGroups) ? nextGroups.filter((item): item is string => typeof item === "string").join(", ") : "",
    );
    setStatusHeadersText(dictToText((config.status as { headers?: Record<string, string> } | null)?.headers));
    setNoticeUrl(
      typeof (config.notice as { url?: unknown } | null | undefined)?.url === "string"
        ? (config.notice as { url: string }).url
        : "",
    );
    setNoticeHeadersText(dictToText((config.notice as { headers?: Record<string, string> } | null)?.headers));
    setModels((config.models ?? []).filter((item): item is string => typeof item === "string"));
    setEndpointHeadersText(dictToText(config.network?.headers));
    const refresh = config.token_refresh ?? null;
    setRefreshMethod(typeof refresh?.method === "string" ? refresh.method.toUpperCase() : "POST");
    setRefreshUrl(typeof refresh?.url === "string" ? refresh.url : "");
    setRefreshToken(typeof refresh?.refresh_token === "string" ? refresh.refresh_token : "");
    setRefreshBody(typeof refresh?.body === "string" ? refresh.body : "");
    setRefreshSample(typeof refresh?.response_sample === "string" ? refresh.response_sample : "");
    setAccessTokenField(typeof refresh?.access_token_field === "string" ? refresh.access_token_field : "");
    setRefreshTokenField(typeof refresh?.refresh_token_field === "string" ? refresh.refresh_token_field : "");
    setHeadless(headlessFromConfig(config));
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

  function formState(): SiteFormState {
    return { id, url, statusUrl, statusGroupsText, noticeUrl, models, endpointHeadersText };
  }

  // 真实调用一次续签接口（由认证子弹窗触发）：验证地址、凭证、响应结构是否都能对上；
  // 返回新 token 交给子弹窗暂存，点确定才随表单一并写回草稿
  async function runRefreshTest(overrides: RefreshOverride): Promise<{ ok: boolean; text: string; result?: RefreshTestResult }> {
    const config = buildConfig();
    if (!config) return { ok: false, text: "高级配置 JSON 格式不对，改好才能测试" };
    const targetRefresh = tokenRefreshConfig(overrides);
    if (!targetRefresh) return { ok: false, text: "先填续签接口地址再测试" };
    try {
      const result = await apiSend<RefreshTestResult>("/api/sites/test-token-refresh", "POST", {
        config: { ...config, token_refresh: targetRefresh },
      });
      return {
        ok: true,
        text: `通了，新 token 已填入，点保存生效${result.refresh_token_rotated ? "（Refresh Token 已换新）" : ""}`,
        result,
      };
    } catch (error) {
      return { ok: false, text: `测试失败：${errorText(error)}` };
    }
  }

  // 认证子弹窗确定：表单字段 + 测试续签拿到的新 token 一并写回草稿
  function commitAuth(fields: AuthFields, rotation?: RefreshTestResult) {
    setRefreshMethod(fields.method);
    setRefreshUrl(fields.url);
    setRefreshToken(fields.token);
    setRefreshBody(fields.body);
    setRefreshSample(fields.sample);
    setAccessTokenField(fields.accessTokenField);
    setRefreshTokenField(fields.refreshTokenField);
    syncTokenRefresh({
      method: fields.method,
      url: fields.url,
      refresh_token: fields.token,
      body: fields.body,
      response_sample: fields.sample,
      access_token_field: fields.accessTokenField,
      refresh_token_field: fields.refreshTokenField,
    });
    if (rotation) syncAdvanced((base) => applyRefreshResult(base, rotation));
    setActiveSub(null);
  }

  // 价格采集子弹窗确定：价格接口（地址/请求头/无头浏览器）与倍率接口（地址/请求头）一并写回草稿
  function commitPrice(next: PriceFields) {
    setUrl(next.url);
    setEndpointHeadersText(next.headersText);
    setHeadless(next.headless);
    setRatioUrl(next.ratioUrl);
    setRatioHeadersText(next.ratioHeadersText);
    syncAdvanced((base) => {
      const network = { ...(base.network ?? {}) } as Record<string, unknown>;
      applyEntryUrl(network, "url", next.url);
      setOptionalDict(network, "headers", next.headersText);
      // 倍率接口：有专用请求头时存成对象，否则存纯地址（兼容旧配置的简单形态）
      const ratioTrimmed = next.ratioUrl.trim();
      const ratioHeaders = textToDict(next.ratioHeadersText);
      if (ratioTrimmed) {
        network.ratio_url =
          Object.keys(ratioHeaders).length > 0 ? { url: ratioTrimmed, headers: ratioHeaders } : ratioTrimmed;
      } else {
        delete network.ratio_url;
      }
      // 无头浏览器：开关关且内容全空时移除该字段
      const hasData = next.headless.cookies.length > 0 || next.headless.localStorage.length > 0;
      if (next.headless.enabled || hasData) {
        network.headless = {
          enabled: next.headless.enabled,
          ...(next.headless.cookies.length > 0
            ? { cookies: next.headless.cookies.map((item) => ({ name: item.name.trim(), value: item.value })) }
            : {}),
          ...(next.headless.localStorage.length > 0
            ? { localStorage: Object.fromEntries(next.headless.localStorage.map((item) => [item.key.trim(), item.value])) }
            : {}),
          wait_seconds: Number(next.headless.waitSeconds) || 0,
        };
      } else {
        delete network.headless;
      }
      return { ...base, network: network as SiteConfig["network"] };
    });
    setActiveSub(null);
  }

  // 渠道状态子弹窗确定：地址清空移除整个 status，有地址时合并分组与专用请求头
  function commitStatus(nextUrl: string, nextGroupsText: string, nextHeadersText: string) {
    setStatusUrl(nextUrl);
    setStatusGroupsText(nextGroupsText);
    setStatusHeadersText(nextHeadersText);
    syncAdvanced((base) => {
      const trimmed = nextUrl.trim();
      if (!trimmed) {
        const { status: _dropped, ...rest } = base;
        return rest as SiteConfig;
      }
      const existing = typeof base.status === "object" && base.status !== null ? base.status : {};
      const groups = nextGroupsText
        .split(/[,，\n]/)
        .map((item) => item.trim())
        .filter(Boolean);
      const status: Record<string, unknown> = { ...existing, url: trimmed };
      if (groups.length > 0) status.groups = groups;
      else delete status.groups;
      const headers = textToDict(nextHeadersText);
      if (Object.keys(headers).length > 0) status.headers = headers;
      else delete status.headers;
      return { ...base, status };
    });
    setActiveSub(null);
  }

  // 站点公告子弹窗确定：地址清空移除整个 notice，有地址时合并专用请求头
  function commitNotice(nextUrl: string, nextHeadersText: string) {
    setNoticeUrl(nextUrl);
    setNoticeHeadersText(nextHeadersText);
    syncAdvanced((base) => {
      const trimmed = nextUrl.trim();
      if (!trimmed) {
        const { notice: _dropped, ...rest } = base;
        return rest as SiteConfig;
      }
      const existing = typeof base.notice === "object" && base.notice !== null ? base.notice : {};
      const notice: Record<string, unknown> = { ...existing, url: trimmed };
      const headers = textToDict(nextHeadersText);
      if (Object.keys(headers).length > 0) notice.headers = headers;
      else delete notice.headers;
      return { ...base, notice };
    });
    setActiveSub(null);
  }

  const headlessConfigured = headless.enabled || headless.cookies.length > 0 || headless.localStorage.length > 0;
  // 地址输入框旁的提示随网页模式切换：开无头浏览器后页面可以要登录
  const urlHint = headless.enabled
    ? "将用无头浏览器打开页面并注入上面的 Cookie 和登录信息，可采集需要登录的页面"
    : "站点提供价格数据的接口地址；要带参数就直接拼在地址后面，如 ?page=1&lang=zh。填网页地址（而不是接口）时，该页面必须无需登录就能打开——我们不会像浏览器那样带上你的登录状态";

  async function save() {
    if (refreshUrlError || refreshBodyError || refreshSampleError) {
      toast("续签配置里还有标红的填写问题，改好再保存");
      return;
    }
    if (refreshUrl.trim() && !refreshToken.trim()) {
      toast("配了续签接口就要填 Refresh Token，续签全靠它换新凭证");
      return;
    }
    const config = buildConfig();
    if (!config) return;
    // 地址清空 = 移除续签配置（同步时不动 JSON，统一在保存时落地）
    if (!refreshUrl.trim() && config.token_refresh) delete config.token_refresh;
    // 打开编辑期间续签可能已自动跑过、服务端换了新的 refresh_token；
    // 用户没改过这个输入框时以服务端最新值为准，避免旧值覆盖回去
    let note = "";
    if (!isNew) {
      try {
        const { sites } = await apiSend<{ sites: SiteConfig[] }>("/api/sites", "GET");
        const server = (sites ?? []).find((item) => item.id === originalId);
        const serverToken =
          typeof server?.token_refresh?.refresh_token === "string" ? server.token_refresh.refresh_token : "";
        const initialToken =
          typeof initial.token_refresh?.refresh_token === "string" ? initial.token_refresh.refresh_token : "";
        if (serverToken && serverToken !== initialToken && refreshToken.trim() === initialToken.trim() && server?.token_refresh) {
          config.token_refresh = { ...(config.token_refresh ?? server.token_refresh), refresh_token: serverToken };
          setRefreshToken(serverToken);
          note = "续签凭证在编辑期间已自动换新，已采用最新值";
        }
      } catch {
        // 拉不到服务端最新配置就以表单为准
      }
    }
    setSaving(true);
    try {
      const saved = isNew
        ? await apiSend<{ warning?: string }>("/api/sites", "POST", { config })
        : await apiSend<{ warning?: string }>(`/api/sites/${encodeURIComponent(originalId)}`, "PUT", { config });
      toast([saved.warning, note].filter(Boolean).join("；") || "站点已保存");
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
        title={isNew ? "新增站点" : `编辑站点：${originalId}`}
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
          <SettingRow label="采集地址" hint={urlHint}>
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
          <SettingRow label="目标模型" hint="从 models.dev 目录搜索勾选；站点自己的别名或新模型，输入后回车也能加">
            <ModelMultiSelect
              value={models}
              onChange={(next) => {
                setModels(next);
                syncAdvanced((base) => ({ ...base, models: next }));
              }}
            />
          </SettingRow>

          {/* 入口卡片：点击打开对应子弹窗，确定才写回草稿 */}
          <div style={{ display: "grid", gap: 8, gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))" }}>
            <EntryCard
              title="价格采集"
              desc="价格接口、请求头、无头浏览器与倍率"
              configured={Boolean(url.trim() || endpointHeadersText.trim() || headlessConfigured || ratioUrl.trim())}
              onClick={() => setActiveSub("price")}
            />
            <EntryCard
              title="认证与续签"
              desc="填 Token 续签接口，token 失效自动换新"
              configured={Boolean(refreshUrl.trim())}
              onClick={() => setActiveSub("auth")}
            />
            <EntryCard
              title="渠道状态"
              desc="顺带检查各渠道是否正常"
              configured={Boolean(statusUrl.trim())}
              onClick={() => setActiveSub("status")}
            />
            <EntryCard
              title="站点公告"
              desc="公告有变化时记录并通知"
              configured={Boolean(noticeUrl.trim())}
              onClick={() => setActiveSub("notice")}
            />
            <EntryCard
              title="高级 JSON"
              desc="直接编辑整份站点配置"
              configured={false}
              onClick={() => setActiveSub("json")}
            />
          </div>
          {advancedError && (
            <div style={{ display: "flex", justifyContent: "flex-end" }}>
              <span style={{ fontSize: 12, color: "var(--tone-red-text)" }}>JSON 格式错误</span>
            </div>
          )}
        </div>
      </Modal>

      {/* 子弹窗：进入时以当前草稿为初值，确定才写回，取消直接丢弃 */}
      {activeSub === "price" && (
        <PriceSubModal
          initial={{
            url,
            headersText: endpointHeadersText,
            headless,
            ratioUrl,
            ratioHeadersText,
          }}
          warningKeys={headerOverrideWarnings.price}
          onCommit={commitPrice}
          onClose={() => setActiveSub(null)}
        />
      )}
      {activeSub === "auth" && (
        <AuthSubModal
          initial={{
            method: refreshMethod,
            url: refreshUrl,
            token: refreshToken,
            body: refreshBody,
            sample: refreshSample,
            accessTokenField,
            refreshTokenField,
          }}
          runTest={runRefreshTest}
          onCommit={commitAuth}
          onClose={() => setActiveSub(null)}
        />
      )}
      {activeSub === "status" && (
        <StatusSubModal
          initialUrl={statusUrl}
          initialGroups={statusGroupsText}
          initialHeadersText={statusHeadersText}
          warningKeys={headerOverrideWarnings.status}
          onCommit={commitStatus}
          onClose={() => setActiveSub(null)}
        />
      )}
      {activeSub === "notice" && (
        <NoticeSubModal
          initialUrl={noticeUrl}
          initialHeadersText={noticeHeadersText}
          warningKeys={headerOverrideWarnings.notice}
          onCommit={commitNotice}
          onClose={() => setActiveSub(null)}
        />
      )}
      {activeSub === "json" && (
        <JsonSubModal initial={advanced} onCommit={onAdvancedChange} onClose={() => setActiveSub(null)} />
      )}
    </>
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
      // 站点名 + 公告/状态/倍率标注标签的最小实宽，窄了标签会把名字挤换行
      width: 200,
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
            scrollX={910}
            mobileScrollX={608}
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
