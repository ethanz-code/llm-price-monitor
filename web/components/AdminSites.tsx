"use client";

/** 站点管理：列表、启停、编辑与删除；数据在浏览器侧拉取管理员接口。 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { toast, Btn, Check, Empty, Input, Modal, Seg, Sel, Switch, Tip } from "./ui";
import { IconAppstore, IconCheck } from "./icons";
import { DataTable, type DColumn } from "./DataTable";
import { apiSend } from "@/lib/api";
import { getSiteInfo } from "@/lib/sites";
import { RiskLink } from "./RiskLink";
import { formatTime, looseIncludes } from "@/lib/format";
import { ToneTag } from "./ToneTag";
import { SiteTestButton } from "./SiteTestButton";
import type { CatalogData, SiteCollectHealth, SiteCollectIssue, SiteConfig, SitesData } from "@/lib/types";

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

/** 分组白名单文本 → 分组数组：中英文逗号、换行都算分隔，空项跳过。 */
function groupsFromText(text: string): string[] {
  return text
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
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
  // 分组白名单是站点级配置：价格采集与渠道状态共用，没有状态接口地址也要保留
  const groups = form.statusGroupsText
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
  if (statusUrl || groups.length > 0) {
    const existingStatus =
      typeof base.status === "object" && base.status !== null && !Array.isArray(base.status) ? base.status : {};
    const status: Record<string, unknown> = { ...existingStatus };
    if (statusUrl) status.url = statusUrl;
    else delete status.url;
    if (groups.length > 0) status.groups = groups;
    else delete status.groups;
    next.status = status;
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

/** 多选下拉的候选项：id 是勾选与标签用的值，note 是右侧的补充说明（模型名/厂商等）。 */
interface MultiOption {
  id: string;
  note?: string;
}

/** 一次最多渲染的匹配项：候选数百条时全部渲染没必要。 */
const MODEL_MATCH_LIMIT = 60;

const MODEL_HINT_STYLE: React.CSSProperties = { fontSize: 12.5, color: "var(--text-3)", padding: "6px 8px" };

/** 标签式多选通用壳：已选项以标签展示、点 × 移除；下拉里输入过滤、勾选候选、回车把输入加为自定义项。
 *  options 为 null 表示候选还在加载，空数组表示没有候选只能手动输入。文案由调用方给，模型目录与分组白名单共用。 */
function TagMultiSelect({
  value,
  onChange,
  options,
  failed,
  ariaLabel,
  inputLabel,
  fieldPlaceholder,
  inputPlaceholder,
  loadingText,
  emptyText,
  failedText,
  noMatchText,
}: {
  value: string[];
  onChange: (next: string[]) => void;
  options: MultiOption[] | null;
  /** 候选拉取失败：空态文案换成"没拉到"口径 */
  failed?: boolean;
  ariaLabel: string;
  inputLabel: string;
  fieldPlaceholder: string;
  inputPlaceholder: string;
  loadingText: string;
  emptyText: string;
  failedText: string;
  noMatchText: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  const selected = useMemo(() => new Set(value), [value]);
  const keyword = query.trim().toLowerCase();
  const matches = useMemo(() => {
    const source = options ?? [];
    if (!keyword) return source.slice(0, MODEL_MATCH_LIMIT);
    const hit = source.filter((item) => looseIncludes(item.id, keyword) || looseIncludes(item.note ?? "", keyword));
    // 手动添加的名字不在候选里：把命中的已选项补进来，保证始终可见可移除
    for (const id of value) {
      if (looseIncludes(id, keyword) && !hit.some((item) => item.id === id)) {
        hit.unshift({ id });
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
        aria-label={ariaLabel}
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
        {value.length === 0 && <span style={{ color: "var(--text-3)", fontSize: 13 }}>{fieldPlaceholder}</span>}
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
                aria-label={inputLabel}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") commitInput();
                }}
                placeholder={inputPlaceholder}
                style={{ height: 30, fontSize: 12.5 }}
              />
            </div>
            <div style={{ maxHeight: 240, overflowY: "auto", padding: 6, display: "grid", gap: 2 }}>
              {options === null && <span style={MODEL_HINT_STYLE}>{loadingText}</span>}
              {options !== null && options.length === 0 && (
                <span style={MODEL_HINT_STYLE}>{failed ? failedText : emptyText}</span>
              )}
              {options !== null && matches.length === 0 && <span style={MODEL_HINT_STYLE}>{noMatchText}</span>}
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
                    {item.note && (
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
                        {item.note}
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

/** 目标模型多选：搜索勾选 models.dev 官方目录，目录外的名字输入后回车添加。 */
function ModelMultiSelect({ value, onChange }: { value: string[]; onChange: (next: string[]) => void }) {
  const [options, setOptions] = useState<MultiOption[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    apiSend<CatalogData>("/api/catalog", "GET")
      .then((data) => {
        if (cancelled) return;
        setOptions(
          Object.values(data.models ?? {})
            .filter((entry) => typeof entry.model === "string" && entry.model)
            .map((entry) => ({
              id: entry.model,
              note: [entry.name ?? "", entry.vendor ?? ""].filter(Boolean).join(" · ") || undefined,
            }))
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

  return (
    <TagMultiSelect
      value={value}
      onChange={onChange}
      options={options}
      failed={failed}
      ariaLabel="选择目标模型"
      inputLabel="搜索或添加模型"
      fieldPlaceholder="点开搜索勾选模型，自定义的也能加"
      inputPlaceholder="搜模型名或厂商；回车把输入添加为自定义模型"
      loadingText="模型目录加载中…"
      emptyText="目录是空的，直接输入模型名回车添加"
      failedText="模型目录还没同步好，直接输入模型名回车添加"
      noMatchText="没有匹配的模型；回车把当前输入添加为自定义模型"
    />
  );
}

/** 分组白名单多选：候选是站点已采集价格数据里出现过的分组名（后端去重）；
 *  新站点还没采过数据时没有候选，直接输入分组名回车添加。 */
function GroupMultiSelect({ siteId, value, onChange }: { siteId: string; value: string[]; onChange: (next: string[]) => void }) {
  const [options, setOptions] = useState<MultiOption[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    // 新站点没有已保存的 id，不请求；编辑时按保存时的站点 id 取已采集分组
    if (!siteId) {
      setOptions([]);
      return;
    }
    let cancelled = false;
    apiSend<{ groups: string[] }>(`/api/sites/${encodeURIComponent(siteId)}/groups`, "GET")
      .then((data) => {
        if (cancelled) return;
        setOptions((data.groups ?? []).filter((item) => typeof item === "string" && item).map((id) => ({ id })));
      })
      .catch(() => {
        if (cancelled) return;
        setOptions([]);
        setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [siteId]);

  return (
    <TagMultiSelect
      value={value}
      onChange={onChange}
      options={options}
      failed={failed}
      ariaLabel="选择分组白名单"
      inputLabel="搜索或添加分组"
      fieldPlaceholder="点开勾选已采集的分组，直接输入回车也能加"
      inputPlaceholder="输入过滤分组；回车把输入添加为白名单"
      loadingText="已采集分组加载中…"
      emptyText="还没有采集到分组，直接输入分组名回车添加"
      failedText="分组列表没拉到，直接输入分组名回车添加"
      noMatchText="没有匹配的分组；回车把当前输入添加为白名单"
    />
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
  headers_text: string;
  refresh_cookie_name: string;
}>;

/** 测试续签接口的成功返回：新 access_token 用于回填各处认证头，refresh_token 可能已被服务端换新 */
type RefreshTestResult = { access_token: string; refresh_token: string; refresh_token_rotated: boolean };

/** 凭证注入的三个目标：与后端 AUTH_INJECT_TARGETS 一致 */
type InjectTarget = "price" | "status" | "notice";

const INJECT_TARGETS: InjectTarget[] = ["price", "status", "notice"];

const INJECT_LABELS: Record<InjectTarget, string> = {
  price: "价格采集",
  status: "渠道状态",
  notice: "站点公告",
};

/** 注入值里可引用的凭证变量（与后端 config.ACCESS_TOKEN_VAR / REFRESH_TOKEN_VAR 一致） */
const ACCESS_VAR = "${access_token}";
const REFRESH_VAR = "${refresh_token}";

/** 一条凭证注入规则：头名（Authorization / cookie / 任意）+ 值模板（可用 ${access_token}、${refresh_token}） */
type InjectRule = { header: string; value: string };

/** 认证与续签子弹窗提交回主弹窗的字段集合 */
type AuthFields = {
  mode: AuthMode;
  authToken: string;
  method: string;
  url: string;
  token: string;
  body: string;
  sample: string;
  accessTokenField: string;
  refreshTokenField: string;
  headersText: string;
  cookieName: string;
  inject: Record<InjectTarget, InjectRule>;
};

/** 价格采集子弹窗提交回主弹窗的字段集合 */
type PriceFields = {
  url: string;
  headersText: string;
  headless: HeadlessForm;
  ratioUrl: string;
  ratioHeadersText: string;
};

/** 网页模式·无头浏览器子弹窗的表单数据（localStorage/headers 用行数组方便增删） */
type HeadlessForm = {
  enabled: boolean;
  cookies: { name: string; value: string }[];
  localStorage: { key: string; value: string }[];
  headers: { key: string; value: string }[];
  waitSeconds: string;
};

/** 站点配置 → 凭证注入规则初值。
 *  没配过 auth_inject 的老站点按当前生效的老行为回显（Authorization: Bearer <access token>），
 *  用户看到的规则就是实际发出去的头，改完保存即收编进 auth_inject。 */
function injectFromConfig(
  config: Pick<SiteConfig, "auth_header" | "auth_prefix" | "auth_inject">,
): Record<InjectTarget, InjectRule> {
  const legacyHeader =
    typeof config.auth_header === "string" && config.auth_header.trim() ? config.auth_header.trim() : "Authorization";
  const legacyPrefix = typeof config.auth_prefix === "string" ? config.auth_prefix : "Bearer ";
  const legacy: InjectRule = { header: legacyHeader, value: `${legacyPrefix}\${access_token}` };
  return Object.fromEntries(
    INJECT_TARGETS.map((target) => {
      const rule = config.auth_inject?.[target];
      const header = typeof rule?.header === "string" ? rule.header : "";
      const value = typeof rule?.value === "string" ? rule.value : "";
      return [target, header.trim() && value.trim() ? { header, value } : { ...legacy }];
    }),
  ) as Record<InjectTarget, InjectRule>;
}

/** 表单里的注入规则 → 站点配置：头名或值为空的整条丢掉（那处不注入） */
function injectRules(inject: Record<InjectTarget, InjectRule>): Record<string, { header: string; value: string }> | null {
  const rules = Object.fromEntries(
    INJECT_TARGETS.filter((target) => inject[target].header.trim() && inject[target].value.trim()).map((target) => [
      target,
      { header: inject[target].header.trim(), value: inject[target].value.trim() },
    ]),
  );
  return Object.keys(rules).length > 0 ? rules : null;
}

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
    headers: Object.entries(headless?.headers ?? {}).map(([key, value]) => ({
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

/** 认证统一收口后，各接口 headers 里写死的 Authorization 全部移除：
 *  认证只走站点级 auth_token 一处（价格/状态/公告自动带上），避免哪个接口残留旧 token 把请求头盖回旧值。 */
function stripHardcodedAuth(base: SiteConfig): { config: SiteConfig; removed: boolean } {
  let removed = false;
  const cleanEntry = (entry: Record<string, unknown>): Record<string, unknown> => {
    const headers = entry.headers;
    if (!headers || typeof headers !== "object") return entry;
    const all = headers as Record<string, unknown>;
    const kept = Object.entries(all).filter(([key]) => key.toLowerCase() !== "authorization");
    if (kept.length === Object.keys(all).length) return entry;
    removed = true;
    const next = { ...entry };
    if (kept.length > 0) next.headers = Object.fromEntries(kept);
    else delete next.headers;
    return next;
  };
  const network = base.network ? cleanEntry({ ...base.network }) : base.network;
  if (network && typeof network.ratio_url === "object" && network.ratio_url !== null) {
    network.ratio_url = cleanEntry({ ...(network.ratio_url as Record<string, unknown>) });
  }
  const config: SiteConfig = {
    ...base,
    network: network as SiteConfig["network"],
    networks: (base.networks ?? []).map((entry) => cleanEntry({ ...entry }) as typeof entry),
    status: base.status ? (cleanEntry({ ...base.status }) as SiteConfig["status"]) : base.status,
    notice: base.notice ? (cleanEntry({ ...base.notice }) as SiteConfig["notice"]) : base.notice,
  };
  return { config, removed };
}

/** 主弹窗中部的入口卡片：点击打开对应子弹窗；configured 时标注"已配置" */function EntryCard({
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
      <span style={{ fontSize: 12, color: "var(--text-3)", wordBreak: "break-all" }} title={desc}>
        {desc}
      </span>
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
  hint = "只发给这个地址；认证头在「认证与续签 → 凭证注入」里配，这里写的同名头会被它盖掉",
  keyPlaceholder = "名字，如 X-API-Key",
  valuePlaceholder = "值",
}: {
  rows: KvRow[];
  onChange: (next: KvRow[]) => void;
  warningKeys: string[];
  /** 传 null 表示这条规则已在同一弹窗里说过一次，不再重复 */
  hint?: string | null;
  keyPlaceholder?: string;
  valuePlaceholder?: string;
}) {
  const authKeys = warningKeys.filter((key) => !/cookie/i.test(key));
  const cookieKeys = warningKeys.filter((key) => /cookie/i.test(key));
  return (
    <div style={{ display: "grid", gap: 6 }}>
      <span style={{ fontSize: 13.5 }}>请求头</span>
      {hint && <span style={{ fontSize: 12, color: "var(--text-3)" }}>{hint}</span>}
      {authKeys.length > 0 && (
        <span style={{ fontSize: 12, color: "var(--tone-yellow-text)" }}>
          {authKeys.join("、")} 里写死了认证头；认证统一在「认证与续签 → 凭证注入」里配，
          这里写的同名头请求时会被它盖掉（保存时也会移除）
        </span>
      )}
      {cookieKeys.length > 0 && (
        <span style={{ fontSize: 12, color: "var(--tone-yellow-text)" }}>
          {cookieKeys.join("、")} 里写死了 Cookie；凭证注入没往这处塞 Cookie 时照常发送，塞了则以注入的为准
        </span>
      )}
      <KeyValueRows rows={rows} onChange={onChange} keyPlaceholder={keyPlaceholder} valuePlaceholder={valuePlaceholder} ariaPrefix="请求头" />
    </div>
  );
}

/* ---------- 子弹窗：渠道状态 ---------- */

function StatusSubModal({
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
      title="渠道状态"
      width={560}
      footer={
        <SubModalFooter
          onCancel={onClose}
          onConfirm={() => onCommit(url, rowsToText(headerRows))}
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
          hint="留空会自动抓站点的 /api/notice（new-api/one-api 都是这个地址）；公告内容有变化时会记成事件。公告请求会自动带上站点认证，不用重复填"
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

/** 用户常把浏览器里整段 Cookie 原样贴进来：去掉 cookie: 头前缀和 new_api_refresh= 名字前缀，只留纯凭据 */
function cleanRefreshToken(text: string, cookieName: string): string {
  let value = text.trim();
  const headerPrefix = value.match(/^cookie\s*:\s*/i);
  if (headerPrefix) value = value.slice(headerPrefix[0].length).trim();
  const name = (cookieName.trim() || "new_api_refresh").toLowerCase();
  if (value.includes(";")) {
    // 整条 Cookie 串（session=…; new_api_refresh=…）：挑出轮换 Cookie 那一段
    for (const pair of value.split(";")) {
      const [key, ...rest] = pair.trim().split("=");
      if (key.trim().toLowerCase() === name && rest.length > 0) return rest.join("=");
    }
    return value;
  }
  const eq = value.indexOf("=");
  if (eq > 0 && value.slice(0, eq).trim().toLowerCase() === name) return value.slice(eq + 1).trim();
  return value;
}

function AuthSubModal({
  initial,
  priceUrl,
  runTest,
  onCommit,
  onClose,
}: {
  /** 打开时的草稿初值；认证方式不传，由字段反推 */
  initial: Omit<AuthFields, "mode">;
  /** 价格采集地址：登录会话模板的续签域名从它推导 */
  priceUrl: string;
  /** 真实调用续签接口；由主弹窗基于当前草稿组装完整配置发请求 */
  runTest: (overrides: RefreshOverride) => Promise<{ ok: boolean; text: string; result?: RefreshTestResult }>;
  onCommit: (fields: AuthFields, rotation?: RefreshTestResult) => void;
  /** 关闭时带回未落草稿的换新凭证（轮换型凭据取消也不能丢）；主弹窗只接凭证、其他改动照旧丢弃 */
  onClose: (rotation?: RefreshTestResult) => void;
}) {
  // 认证方式由当前配置反推：配了续签是登录会话，配了站点令牌是固定令牌
  const [mode, setModeState] = useState<AuthMode>(() =>
    initial.url.trim() || initial.cookieName.trim() ? "session" : initial.authToken.trim() ? "token" : "none",
  );
  const [authToken, setAuthToken] = useState(initial.authToken);
  const [method, setMethod] = useState(initial.method);
  const [url, setUrl] = useState(initial.url);
  const [token, setToken] = useState(initial.token);
  const [body, setBody] = useState(initial.body);
  const [sample, setSample] = useState(initial.sample);
  const [headersRows, setHeadersRows] = useState<KvRow[]>(dictToRows(initial.headersText));
  const [cookieName, setCookieName] = useState(initial.cookieName);
  // AI 分析出的字段路径没有表单入口，展示出来让用户知道分析到了什么；模板预填时本地补默认值，确定才回写
  const [accessTokenField, setAccessTokenField] = useState(initial.accessTokenField);
  const [refreshTokenField, setRefreshTokenField] = useState(initial.refreshTokenField);
  // 凭证注入规则：把上面的 token 塞进价格/渠道状态/公告三处请求，头名与值都可改
  const [inject, setInject] = useState<Record<InjectTarget, InjectRule>>(initial.inject);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);
  // 测试成功后暂存的新 token：点确定才随表单一并回写主弹窗草稿，取消则全部丢弃
  const [rotation, setRotation] = useState<RefreshTestResult | null>(null);
  // 标准 new-api 模板（GET + Cookie 凭据、无请求体）配好时高级选项整体收起：贴凭据 → 测试 就完了；
  // 模板可能在弹窗里切换方式时才带出，所以动态判定，用户手动展开/收起后以手动为准
  const [advancedOverride, setAdvancedOverride] = useState<boolean | null>(null);
  // 凭据获取步骤默认折叠，主界面只留一句"凭据是什么"
  const [showHowTo, setShowHowTo] = useState(false);
  const standardTemplate =
    url.trim() !== "" &&
    body.trim() === "" &&
    cookieName.trim().toLowerCase() === "new_api_refresh" &&
    headersRows.some((row) => row.key.trim().toLowerCase() === "cookie" && row.value.includes("new_api_refresh="));
  const showAdvanced = advancedOverride ?? !standardTemplate;
  const headersText = rowsToText(headersRows);

  // 切到登录会话且续签还空白时，带出 new-api 型模板（域名从价格采集地址推导）；已填过的一律不动
  function setMode(next: string) {
    const nextMode: AuthMode = next === "session" ? "session" : next === "token" ? "token" : "none";
    setModeState(nextMode);
    setTestResult(null);
    if (nextMode !== "session") return;
    if (url.trim() || cookieName.trim()) return;
    const origin = originOf(priceUrl);
    if (origin) setUrl(`${origin}/api/user/auth/refresh`);
    if (headersRows.every((row) => !row.key.trim() && !row.value.trim())) {
      setHeadersRows([{ key: "cookie", value: "new_api_refresh=${refresh_token}" }]);
    }
    if (!cookieName.trim()) setCookieName("new_api_refresh");
  }

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

  // 凭证注入即时校验：头名和值成对填；引用 ${refresh_token} 需要「登录会话」模式才有值
  const injectError = (() => {
    const half = INJECT_TARGETS.find((target) => Boolean(inject[target].header.trim()) !== Boolean(inject[target].value.trim()));
    return half ? `${INJECT_LABELS[half]}的头名和值要成对填；那处不想注入就两个都清空` : "";
  })();
  const injectWarning =
    mode === "token" && INJECT_TARGETS.some((target) => inject[target].value.includes(REFRESH_VAR))
      ? `「固定令牌」没有 Refresh Token，值里引用 ${REFRESH_VAR} 的规则采集时会报错；要用它就把认证方式换成「登录会话自动续签」`
      : "";

  const overrides = (): RefreshOverride => ({
    method,
    url,
    refresh_token: cleanRefreshToken(token, cookieName),
    body,
    response_sample: sample,
    headers_text: headersText,
    refresh_cookie_name: cookieName,
  });
  const fields = (): AuthFields => ({
    mode,
    authToken,
    method,
    url,
    token: cleanRefreshToken(token, cookieName),
    body,
    sample,
    accessTokenField,
    refreshTokenField,
    headersText,
    cookieName,
    inject,
  });

  function onMethodChange(next: string) {
    setMethod(next);
    setTestResult(null);
  }

  function onUrlChange(next: string) {
    setUrl(next);
    setTestResult(null);
  }

  // 真实调用一次续签接口：验证地址、凭证、响应结构是否都能对上
  async function test() {
    if (!url.trim()) {
      setTestResult({ ok: false, text: "先点开「续签接口」填上地址再测试" });
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const outcome = await runTest(overrides());
      setTestResult({ ok: outcome.ok, text: outcome.text });
      if (outcome.ok && outcome.result) {
        // 新 token 先回填到本弹窗输入框，点确定才落草稿：Refresh Token 供下次续签，
        // Access Token 就是采集请求头里带的那份，测试换新的直接填上让用户看得见
        setToken(outcome.result.refresh_token);
        setAuthToken(outcome.result.access_token);
        setRotation(outcome.result);
      }
    } finally {
      setTesting(false);
    }
  }

  function commit() {
    // 登录会话必须有续签地址，否则配置落不下去；不打算配就切回其他方式
    if (mode === "session" && !url.trim()) {
      setTestResult({ ok: false, text: "先点开「续签接口」填上地址；不配认证就把方式切回「无需认证」" });
      return;
    }
    if (injectError) {
      setTestResult({ ok: false, text: injectError });
      return;
    }
    onCommit(fields(), rotation ?? undefined);
  }

  // 取消也把换新凭证带回主弹窗：测试这一下就可能把旧 Refresh Token 作废，丢掉只能回浏览器重新抓。
  // 测试后又手改过任一 token 输入框的，以手改为准，全部丢弃
  function discard() {
    const untouched =
      rotation !== null &&
      token.trim() === rotation.refresh_token &&
      authToken.trim() === rotation.access_token;
    onClose(untouched && rotation ? rotation : undefined);
  }

  return (
    <Modal open onClose={discard} title="认证与续签" width={640} footer={<SubModalFooter onCancel={discard} onConfirm={commit} />}>
      <div style={{ display: "grid", gap: 14 }}>
        {/* 三种方式互斥，选中才显示对应输入区；被切走方式的配置在点确定时清掉 */}
        <SettingRow
          label="认证方式"
          hint={
            mode === "session"
              ? "站点登录 token 短效时，配一个续签接口：价格、渠道状态、公告被拒时自动换新 token 重试"
              : mode === "token"
                ? "填站点级的 API Key，价格、渠道状态、公告采集自动带上认证头"
                : "公开接口不用认证；之后要认证了随时回来切"
          }
        >
          <Seg
            value={mode}
            onChange={setMode}
            options={(Object.keys(AUTH_LABELS) as AuthMode[]).map((value) => ({ value, label: AUTH_LABELS[value] }))}
          />
        </SettingRow>
        {mode === "none" && (
          <span style={{ fontSize: 12, color: "var(--text-3)" }}>
            采集请求不带任何认证；如果某个接口的请求头里手写过凭证，仍按原样发送，不受这里影响
          </span>
        )}
        {mode === "token" && (
          <div style={{ display: "grid", gap: 8 }}>
            <span style={{ fontSize: 13.5 }}>站点令牌</span>
            <span style={{ fontSize: 12, color: "var(--text-3)" }}>
              会自动加到价格、渠道状态、公告所有采集请求的认证头（默认 Authorization: Bearer …）；
              保存后各接口请求头里手写的 Authorization 会自动移除，认证只走这一处
            </span>
            <Input value={authToken} onChange={setAuthToken} placeholder="sk-… 或令牌原文" style={{ width: "100%" }} />
          </div>
        )}
        {mode === "session" && (
          <div style={{ display: "grid", gap: 14 }}>
            <div style={{ display: "grid", gap: 6 }}>
              <div style={{ display: "flex", alignItems: "center" }}>
                <span style={{ fontSize: 13.5, flex: 1 }}>Refresh Token</span>
                <Btn variant="text" size="sm" onClick={() => setShowHowTo(!showHowTo)}>
                  {showHowTo ? "收起" : "怎么获取？"}
                </Btn>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <Input
                  value={token}
                  onChange={(value) => {
                    setToken(value);
                    setTestResult(null);
                  }}
                  placeholder="粘贴 Cookie 里的 new_api_refresh 值，即续签请求里的 ${refresh_token}"
                  style={{ flex: 1, minWidth: 0 }}
                />
                <Btn loading={testing} onClick={test}>
                  测试续签
                </Btn>
              </div>
              {testResult && (
                <span style={{ fontSize: 12.5, color: testResult.ok ? "var(--tone-green-text)" : "var(--tone-red-text)" }}>
                  {testResult.text}
                </span>
              )}
              {showHowTo && (
                <span style={{ fontSize: 12, color: "var(--text-3)" }}>
                  登录站点网页版 → 按 F12 打开开发者工具 → 应用（Application）→ Cookies → 找到 new_api_refresh，复制它的值贴到上面，
                  整段带着 cookie: 前缀直接贴也认得。贴完回浏览器重新登录一次站点，两边各用各的会话；凭据最长 30 天，到期重抓一次更新
                </span>
              )}
            </div>
            <div style={{ display: "grid", gap: 6 }}>
              <span style={{ fontSize: 13.5 }}>Access Token</span>
              <Input
                value={authToken}
                onChange={(value) => {
                  setAuthToken(value);
                  setTestResult(null);
                }}
                placeholder="选填：F12 → 网络 → 任一接口请求头里 Authorization 的 Bearer 后面那段"
                style={{ width: "100%" }}
              />
              <span style={{ fontSize: 12, color: "var(--text-3)" }}>
                采集价格、渠道状态、公告时带的登录凭证就是它（Authorization: Bearer …）；填了立刻能用，留空也行——
                第一次采集被拒时会自动续签补上，之后每次续签也会自动换成新的
              </span>
            </div>
            <button type="button" className="disclosure-row" aria-expanded={showAdvanced} onClick={() => setAdvancedOverride(!showAdvanced)}>
              <span className="caret" aria-hidden>
                ▸
              </span>
              <span style={{ fontWeight: 500, whiteSpace: "nowrap" }}>续签接口</span>
              {url.trim() ? (
                <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--text-3)" }}>
                  {method} {url.trim()}
                </span>
              ) : (
                <span style={{ flex: 1, minWidth: 0, color: "var(--tone-red-text)" }}>未配置，点这行填上</span>
              )}
              {standardTemplate && (
                <span style={{ fontSize: 11, lineHeight: 1.6, padding: "0 7px", borderRadius: 999, background: "var(--tone-blue-bg)", color: "var(--tone-blue-text)", whiteSpace: "nowrap" }}>
                  new-api 模板
                </span>
              )}
            </button>
            {showAdvanced && (
              <div style={{ border: "1px solid var(--border)", borderRadius: 10, background: "color-mix(in srgb, var(--panel-2) 45%, var(--panel))", padding: "10px 12px", display: "grid", gap: 14 }}>
                <div style={{ display: "grid", gap: 4 }}>
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
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ flex: 1, height: 1, background: "var(--border-strong)" }} />
                  <span style={{ fontSize: 12, color: "var(--text-3)", whiteSpace: "nowrap" }}>请求细节（一般不用改）</span>
                  <span style={{ flex: 1, height: 1, background: "var(--border-strong)" }} />
                </div>
                <div style={{ display: "grid", gap: 6 }}>
                  <span style={{ fontSize: 13.5 }}>请求体</span>
                  <Input
                    value={body}
                    onChange={(value) => {
                      setBody(value);
                      setTestResult(null);
                    }}
                    placeholder={'选填：接口要 JSON 参数才填，如 {"refresh_token": "${refresh_token}"}；凭据走 Cookie 的留空'}
                    style={{ width: "100%" }}
                  />
                  {bodyError && <span style={{ fontSize: 12, color: "var(--tone-red-text)" }}>{bodyError}</span>}
                </div>
                <HeadersEditor
                  rows={headersRows}
                  onChange={(next) => {
                    setHeadersRows(next);
                    setTestResult(null);
                  }}
                  warningKeys={[]}
                  hint={null}
                  keyPlaceholder="名字，如 cookie"
                  valuePlaceholder="值，如 new_api_refresh=${refresh_token}，续签自动代入最新值"
                />
                <div style={{ display: "grid", gap: 6 }}>
                  <span style={{ fontSize: 13.5 }}>轮换 Cookie 名</span>
                  <Input
                    value={cookieName}
                    onChange={(value) => {
                      setCookieName(value);
                      setTestResult(null);
                    }}
                    placeholder="如 new_api_refresh；填了能自动接住换新后的凭据，不用回浏览器重抓"
                    style={{ width: "100%" }}
                  />
                </div>
                <div style={{ display: "grid", gap: 6 }}>
                  <span style={{ fontSize: 13.5 }}>响应案例</span>
                  <textarea
                    className="input mono textarea"
                    value={sample}
                    onChange={(event) => setSample(event.target.value)}
                    rows={3}
                    spellCheck={false}
                    placeholder="选填：贴一段续签接口实际返回的 JSON，保存时自动分析新 token 在哪；不贴按常见结构找"
                    style={{ fontSize: 12 }}
                  />
                  {sampleError && <span style={{ fontSize: 12, color: "var(--tone-red-text)" }}>{sampleError}</span>}
                </div>
              </div>
            )}
          </div>
        )}
        {/* 凭证注入：把上面的 token 送到三处采集请求，固定令牌与登录会话都适用 */}
        {mode !== "none" && (
          <div style={{ display: "grid", gap: 8 }}>
            <span style={{ fontSize: 13.5 }}>凭证注入</span>
            <span style={{ fontSize: 12, color: "var(--text-3)" }}>
              决定上面的 token 怎么带进采集请求：头名填 Authorization 就是认证头，填 cookie 就塞进 Cookie；
              值里可以用 {ACCESS_VAR} 和 {REFRESH_VAR}，续签换新后自动跟着变。头名清空 = 那处不带
            </span>
            <div style={{ display: "grid", gap: 8 }}>
              {INJECT_TARGETS.map((target) => (
                <div key={target} style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <span style={{ width: 64, fontSize: 12.5, color: "var(--text-2)", flexShrink: 0 }}>
                    {INJECT_LABELS[target]}
                  </span>
                  <Input
                    value={inject[target].header}
                    ariaLabel={`${INJECT_LABELS[target]}注入的头名`}
                    onChange={(value) => setInject({ ...inject, [target]: { ...inject[target], header: value } })}
                    placeholder="头名，如 Authorization 或 cookie"
                    style={{ flex: 1, minWidth: 0 }}
                  />
                  <Input
                    value={inject[target].value}
                    ariaLabel={`${INJECT_LABELS[target]}注入的值`}
                    onChange={(value) => setInject({ ...inject, [target]: { ...inject[target], value } })}
                    placeholder={`值，如 Bearer ${ACCESS_VAR}`}
                    style={{ flex: 1.6, minWidth: 0 }}
                  />
                </div>
              ))}
            </div>
            {injectError && <span style={{ fontSize: 12, color: "var(--tone-red-text)" }}>{injectError}</span>}
            {injectWarning && <span style={{ fontSize: 12, color: "var(--tone-yellow-text)" }}>{injectWarning}</span>}
          </div>
        )}
      </div>
    </Modal>
  );
}

/* ---------- 子弹窗：网页模式·无头浏览器 ---------- */

/** 键值对编辑区：多行两列输入 + 删除/添加按钮；Cookie 和 localStorage 共用 */
function KeyValueRows({
  rows,
  onChange,
  keyPlaceholder,
  valuePlaceholder,
  ariaPrefix,
}: {
  rows: { key: string; value: string }[];
  onChange: (next: { key: string; value: string }[]) => void;
  keyPlaceholder: string;
  valuePlaceholder: string;
  ariaPrefix: string;
}) {
  // 底部永远留一行空行当"添加"入口（只在显示层补，不进数据）；保存时空行自动忽略
  const last = rows[rows.length - 1];
  const display = !last || last.key.trim() || last.value.trim() ? [...rows, { key: "", value: "" }] : rows;
  return (
    <div style={{ display: "grid", gap: 6 }}>
      {display.map((row, index) => {
        const ghost = index === display.length - 1 && !row.key.trim() && !row.value.trim();
        return (
          <div key={index} style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <Input
              value={row.key}
              ariaLabel={`${ariaPrefix} 名字 ${index + 1}`}
              onChange={(next) => onChange(display.map((item, i) => (i === index ? { ...item, key: next } : item)))}
              placeholder={keyPlaceholder}
              style={{ flex: 1, minWidth: 120 }}
            />
            <Input
              value={row.value}
              ariaLabel={`${ariaPrefix} 值 ${index + 1}`}
              onChange={(next) => onChange(display.map((item, i) => (i === index ? { ...item, value: next } : item)))}
              placeholder={valuePlaceholder}
              style={{ flex: 2, minWidth: 160 }}
            />
            {ghost ? (
              <span style={{ width: 28 }} aria-hidden />
            ) : (
              <Btn
                variant="text"
                size="sm"
                ariaLabel={`删除第 ${index + 1} 条`}
                title="删除"
                onClick={() => onChange(display.filter((_, i) => i !== index))}
              >
                <span style={{ fontSize: 15, lineHeight: 1 }}>×</span>
              </Btn>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** 无头浏览器配置区（受控）：价格采集子弹窗内使用；开关在「采集方式」分段控件上，这里按形态展开内容 */
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
    <div style={{ display: "grid", gap: 14 }}>
      {!value.enabled && (
        <div style={{ display: "grid", gap: 6 }}>
          <span style={{ fontSize: 13.5 }}>自定义请求头</span>
          <span style={{ fontSize: 12, color: "var(--text-3)" }}>
            自动回退无头浏览器时会带上这些请求头（Referer、X-Token 之类），平时普通抓取也会用
          </span>
          <KeyValueRows
            rows={value.headers}
            onChange={(rows) => onChange({ ...value, headers: rows })}
            keyPlaceholder="名字，如 X-Token"
            valuePlaceholder="值"
            ariaPrefix="自定义请求头"
          />
        </div>
      )}
      {value.enabled && (
        <>
          <div style={{ display: "grid", gap: 6 }}>
            <span style={{ fontSize: 13.5 }}>Cookie</span>
            <span style={{ fontSize: 12, color: "var(--text-3)" }}>
              登录站点后按 F12 → 应用（Application）→ Cookies，把需要的键值抄进来；只填名字和值，域名、路径会按采集地址自动带上
            </span>
            <KeyValueRows
              rows={cookieRows}
              onChange={(rows) =>
                onChange({ ...value, cookies: rows.map((row) => ({ name: row.key, value: row.value })) })
              }
              keyPlaceholder="名字，如 session"
              valuePlaceholder="值，如 abc123"
              ariaPrefix="Cookie"
            />
          </div>
          <div style={{ display: "grid", gap: 6 }}>
            <span style={{ fontSize: 13.5 }}>localStorage</span>
            <span style={{ fontSize: 12, color: "var(--text-3)" }}>
              浏览器不会把 localStorage 发给服务器，它只在页面内部被脚本读取；页面的 JS 靠它取登录信息时才需要填
            </span>
            <KeyValueRows
              rows={value.localStorage}
              onChange={(rows) => onChange({ ...value, localStorage: rows })}
              keyPlaceholder="键，如 token"
              valuePlaceholder="值"
              ariaPrefix="localStorage"
            />
          </div>
          <div style={{ display: "grid", gap: 6 }}>
            <span style={{ fontSize: 13.5 }}>自定义请求头</span>
            <KeyValueRows
              rows={value.headers}
              onChange={(rows) => onChange({ ...value, headers: rows })}
              keyPlaceholder="名字，如 X-Token"
              valuePlaceholder="值"
              ariaPrefix="自定义请求头"
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

/* ---------- 子弹窗：价格采集（采集方式 + 价格接口 + 无头浏览器 + 倍率接口） ---------- */

/** 采集方式：接口直采（抓到网页壳才自动换无头） / 网页模式（直接用无头浏览器开页面）。只作用于价格采集 */
type CollectMode = "api" | "browser";

const COLLECT_LABELS: Record<CollectMode, string> = { api: "接口直采", browser: "网页模式（无头浏览器）" };

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

  // 采集方式只影响价格这一路（渠道状态、公告始终走接口），所以收在本弹窗里
  function setCollectMode(next: CollectMode) {
    const enabled = next === "browser";
    if (enabled === headless.enabled) return;
    const waitSeconds = enabled && !headless.waitSeconds.trim() ? "3" : headless.waitSeconds;
    setHeadless({ ...headless, enabled, waitSeconds });
  }

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
        headers: headless.headers.filter((item) => item.key.trim()),
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
        {/* 采集方式只作用于价格采集，模式定了才知道下面该展示接口请求头还是浏览器登录态 */}
        <SettingRow
          label="采集方式"
          hint={
            headless.enabled
              ? "页面要在浏览器里跑 JS 才能显示价格（直接抓是空壳，常见于单页应用）时选它：每次都用真浏览器打开并渲染，Cookie、localStorage 登录态一并注入"
              : "先当接口请求（Cookie 等登录态照常随请求头带上）；抓到的 HTML 里没有价格时自动换无头浏览器重试，一般站点选这个就够"
          }
        >
          <Seg
            value={headless.enabled ? "browser" : "api"}
            onChange={(value) => setCollectMode(value === "browser" ? "browser" : "api")}
            options={(Object.keys(COLLECT_LABELS) as CollectMode[]).map((value) => ({
              value,
              label: COLLECT_LABELS[value],
            }))}
          />
        </SettingRow>
        {/* 上块：价格接口 */}
        <div style={{ display: "grid", gap: 14 }}>
          <SettingRow
            label="价格接口 URL"
            hint="填价格接口地址或网页地址都行；要带参数就直接拼在地址后面，如 ?page=1&lang=zh"
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
          <HeadersEditor rows={ratioHeaderRows} onChange={setRatioHeaderRows} warningKeys={[]} hint={null} />
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

/** 认证方式：无需认证 / 固定令牌 / 登录会话自动续签 */
type AuthMode = "none" | "token" | "session";

const AUTH_LABELS: Record<AuthMode, string> = { none: "无需认证", token: "固定令牌", session: "登录会话自动续签" };

/** 从采集地址取站点域名（new-api 型续签地址按它推导）；不是合法 URL 返回空串 */
function originOf(text: string): string {
  try {
    return new URL(text.trim()).origin;
  } catch {
    return "";
  }
}

/** 采集地址域名 → 站点 ID：小写、非字母数字转连字符（aihub365.cn → aihub365-cn）；解析失败返回空串 */
function siteIdFromUrl(text: string): string {
  try {
    const host = new URL(text.trim()).hostname.replace(/^www\./, "").toLowerCase();
    return host.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  } catch {
    return "";
  }
}

/** 地址太长时截断给入口卡片描述用；去掉协议头让有限宽度里能多看到路径 */
function shortenUrlText(text: string, max = 46): string {
  const bare = text.trim().replace(/^https?:\/\//, "");
  return bare.length > max ? `${bare.slice(0, max)}…` : bare;
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
  // 站点令牌（固定令牌方式的输入值）：提为状态，认证弹窗每次打开都以最新草稿为初值
  const [authToken, setAuthToken] = useState(typeof initial.auth_token === "string" ? initial.auth_token : "");
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
  // 续签专属请求头（cookie 型站点在这里放 new_api_refresh=${refresh_token}）与轮换 Cookie 名
  const [refreshHeadersText, setRefreshHeadersText] = useState(() => dictToText(initial.token_refresh?.headers));
  const [refreshCookieName, setRefreshCookieName] = useState(
    typeof initial.token_refresh?.refresh_cookie_name === "string" ? initial.token_refresh.refresh_cookie_name : "",
  );
  // 网页模式·无头浏览器表单草稿
  const [headless, setHeadless] = useState<HeadlessForm>(() => headlessFromConfig(initial));
  const [activeSub, setActiveSub] = useState<SubKey | null>(null);
  const [advanced, setAdvanced] = useState(JSON.stringify(initial, null, 2));
  const [saving, setSaving] = useState(false);
  const advancedError = advancedJsonError(advanced);
  // 「认证与续签」卡片的已配置状态：续签地址或站点令牌任一存在
  const authTokenConfigured = useMemo(() => {
    try {
      const parsed = JSON.parse(advanced) as SiteConfig;
      return typeof parsed.auth_token === "string" && parsed.auth_token.trim() !== "";
    } catch {
      return false;
    }
  }, [advanced]);
  // 凭证注入初值：以当前草稿为准；高级 JSON 非法时退回默认规则，不打断正在编辑的 JSON
  const injectInitial = useMemo(() => {
    try {
      const parsed = JSON.parse(advanced) as SiteConfig;
      return injectFromConfig(parsed !== null && typeof parsed === "object" ? parsed : {});
    } catch {
      return injectFromConfig({});
    }
  }, [advanced]);

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

  // 认证凭证（auth_token/Cookie）由「认证与续签 → 凭证注入」按目标下发到价格/状态/公告；
  // 哪个接口的 headers 里手写了认证头，请求时就会被注入的头盖掉（保存时也会移除），
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
    const cookieName = (overrides.refresh_cookie_name ?? refreshCookieName).trim();
    const headers = textToDict(overrides.headers_text ?? refreshHeadersText);
    return {
      url: targetUrl,
      method: overrides.method ?? refreshMethod,
      refresh_token: (overrides.refresh_token ?? refreshToken).trim(),
      ...(body ? { body } : {}),
      ...(sample ? { response_sample: sample } : {}),
      ...(atField ? { access_token_field: atField } : {}),
      ...(rtField ? { refresh_token_field: rtField } : {}),
      ...(Object.keys(headers).length > 0 ? { headers } : {}),
      ...(cookieName ? { refresh_cookie_name: cookieName } : {}),
    };
  }

  function syncTokenRefresh(overrides: RefreshOverride = {}) {
    syncAdvanced((base) => {
      const next = tokenRefreshConfig(overrides);
      if (!next) {
        // 清空地址多半是在改地址，此刻删整个 token_refresh 会连带丢掉 refresh_token/请求体；
        // 先不动 JSON，"地址为空 = 移除续签"留到保存时统一处理
        return base;
      }
      const merged = { ...(base.token_refresh ?? {}), ...next } as SiteConfig["token_refresh"];
      // 表单里清空了续签请求头 = 移除；merge 语义覆盖不掉旧键，这里显式删
      if (merged && !("headers" in next) && "headers" in merged) delete merged.headers;
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
    setRefreshHeadersText(dictToText(refresh?.headers));
    setRefreshCookieName(typeof refresh?.refresh_cookie_name === "string" ? refresh.refresh_cookie_name : "");
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
        text: `通了，新 token 已填入，点保存生效${result.refresh_token_rotated ? "；Refresh Token 已换新并自动接住" : ""}`,
        result,
      };
    } catch (error) {
      return { ok: false, text: `测试失败：${errorText(error)}` };
    }
  }

  // 认证子弹窗取消：其他改动照旧丢弃，但测试已换新的 Refresh Token 要接住——
  // 轮换型凭据旧值用一次就作废，丢了只能回浏览器重新抓
  function closeAuth(rotation?: RefreshTestResult) {
    if (rotation) {
      const rotatedRefresh = rotation.refresh_token && rotation.refresh_token !== refreshToken.trim();
      const rotatedAccess = rotation.access_token && rotation.access_token !== authToken.trim();
      if (rotatedRefresh) {
        setRefreshToken(rotation.refresh_token);
        syncAdvanced((base) =>
          base.token_refresh
            ? { ...base, token_refresh: { ...base.token_refresh, refresh_token: rotation.refresh_token } }
            : base,
        );
      }
      if (rotatedAccess) {
        setAuthToken(rotation.access_token);
        syncAdvanced((base) => ({ ...base, auth_token: rotation.access_token }));
      }
      // 服务端已按新 token 落库，草稿跟着同步，下次保存才不会又写回旧值
      if (rotatedRefresh || rotatedAccess) toast("测试换新的 token 已接住，保存站点后生效");
    }
    setActiveSub(null);
  }

  // 价格采集子弹窗确定：价格接口（地址/请求头/无头浏览器）与倍率接口（地址/请求头）一并写回草稿
  function commitPrice(next: PriceFields) {
    setUrl(next.url);
    setEndpointHeadersText(next.headersText);
    setHeadless(next.headless);
    setRatioUrl(next.ratioUrl);
    setRatioHeadersText(next.ratioHeadersText);
    // 站点 ID 留空时按采集地址域名自动生成（如 aihub365.cn → aihub365-cn），少一个必填项
    const derivedId = id.trim() ? "" : siteIdFromUrl(next.url);
    if (derivedId) setId(derivedId);
    // 先选了"登录会话"后填地址：续签地址为空时按站点域名自动补全（表单状态与 JSON 同步）
    if (!refreshUrl.trim() && refreshCookieName.trim()) {
      const origin = originOf(next.url);
      if (origin) setRefreshUrl(`${origin}/api/user/auth/refresh`);
    }
    syncAdvanced((base) => {
      const baseWithId = derivedId && !String(base.id ?? "").trim() ? { ...base, id: derivedId } : base;
      const network = { ...(baseWithId.network ?? {}) } as Record<string, unknown>;
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
      const hasData =
        next.headless.cookies.length > 0 ||
        next.headless.localStorage.length > 0 ||
        next.headless.headers.length > 0;
      if (next.headless.enabled || hasData) {
        network.headless = {
          enabled: next.headless.enabled,
          ...(next.headless.cookies.length > 0
            ? { cookies: next.headless.cookies.map((item) => ({ name: item.name.trim(), value: item.value })) }
            : {}),
          ...(next.headless.localStorage.length > 0
            ? { localStorage: Object.fromEntries(next.headless.localStorage.map((item) => [item.key.trim(), item.value])) }
            : {}),
          ...(next.headless.headers.length > 0
            ? { headers: Object.fromEntries(next.headless.headers.map((item) => [item.key.trim(), item.value])) }
            : {}),
          wait_seconds: Number(next.headless.waitSeconds) || 0,
        };
      } else {
        delete network.headless;
      }
      const nextConfig = { ...baseWithId, network: network as SiteConfig["network"] };
      const refresh = nextConfig.token_refresh;
      if (refresh && !String(refresh.url ?? "").trim() && refresh.refresh_cookie_name) {
        const origin = originOf(next.url);
        if (origin) nextConfig.token_refresh = { ...refresh, url: `${origin}/api/user/auth/refresh` };
      }
      return nextConfig;
    });
    setActiveSub(null);
  }

  // 渠道状态子弹窗确定：地址清空只移除地址与专用请求头，分组白名单是站点级配置、留在主表单
  function commitStatus(nextUrl: string, nextHeadersText: string) {
    setStatusUrl(nextUrl);
    setStatusHeadersText(nextHeadersText);
    syncAdvanced((base) => {
      const existing = typeof base.status === "object" && base.status !== null ? base.status : {};
      const trimmed = nextUrl.trim();
      if (!trimmed) {
        if (existing.groups !== undefined) return { ...base, status: { groups: existing.groups } } as SiteConfig;
        const { status: _dropped, ...rest } = base;
        return rest as SiteConfig;
      }
      const status: Record<string, unknown> = { ...existing, url: trimmed };
      const headers = textToDict(nextHeadersText);
      if (Object.keys(headers).length > 0) status.headers = headers;
      else delete status.headers;
      return { ...base, status };
    });
    setActiveSub(null);
  }

  // 分组白名单输入：站点级过滤，价格采集与渠道状态共用，直接写进高级 JSON 的 status.groups
  function commitGroupsText(nextText: string) {
    setStatusGroupsText(nextText);
    syncAdvanced((base) => {
      const existing = typeof base.status === "object" && base.status !== null ? base.status : {};
      const groups = groupsFromText(nextText);
      const status: Record<string, unknown> = { ...existing };
      if (groups.length > 0) status.groups = groups;
      else delete status.groups;
      if (Object.keys(status).length === 0) {
        const { status: _dropped, ...rest } = base;
        return rest as SiteConfig;
      }
      return { ...base, status };
    });
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

  const headlessConfigured =
    headless.enabled || headless.cookies.length > 0 || headless.localStorage.length > 0 || headless.headers.length > 0;

  // 认证子弹窗确定：按所选认证方式写回草稿。三种方式互斥，被切走方式的配置一并清掉，
  // 否则配置里残留半套认证，重开弹窗时方式又会反推回旧的
  function commitAuth(fields: AuthFields, rotation?: RefreshTestResult) {
    setAuthToken(fields.mode === "none" ? "" : fields.authToken);
    if (fields.mode !== "session") {
      // 无需认证/固定令牌不带续签：续签字段全部清空
      const hadRenewal = Boolean(refreshUrl.trim() || refreshCookieName.trim());
      setRefreshMethod("POST");
      setRefreshUrl("");
      setRefreshToken("");
      setRefreshBody("");
      setRefreshSample("");
      setAccessTokenField("");
      setRefreshTokenField("");
      setRefreshHeadersText("");
      setRefreshCookieName("");
      syncAdvanced((base) => {
        const { token_refresh: _dropped, ...rest } = base;
        return {
          ...rest,
          auth_token: fields.mode === "token" && fields.authToken.trim() ? fields.authToken.trim() : null,
          // 无需认证时不注入；固定令牌模式照常按规则注入
          auth_inject: fields.mode === "token" ? injectRules(fields.inject) : null,
        } as SiteConfig;
      });
      if (hadRenewal) {
        toast(fields.mode === "token" ? "已切换为固定令牌，原续签配置已移除" : "已清除认证，原续签配置已移除");
      } else if (fields.mode === "none" && authTokenConfigured) {
        toast("已清除站点令牌");
      }
      // 站点令牌在管认证了，接口 headers 里写死的 Authorization 一并移除
      if (fields.mode === "token" && fields.authToken.trim()) {
        let removed = false;
        syncAdvanced((base) => {
          const result = stripHardcodedAuth(base);
          removed = removed || result.removed;
          return result.config;
        });
        if (removed) toast("已改为站点统一认证，各接口里写死的 Authorization 已移除");
      }
      setActiveSub(null);
      return;
    }
    // 登录会话：Refresh Token 交给续签换新维护；Access Token 是采集请求头里真正带的那份，
    // 以表单值为准（测试换新的已回填到输入框），用户清空就是不要它、等下次采集续签补上
    setRefreshMethod(fields.method);
    setRefreshUrl(fields.url);
    setRefreshToken(fields.token);
    setRefreshBody(fields.body);
    setRefreshSample(fields.sample);
    setAccessTokenField(fields.accessTokenField);
    setRefreshTokenField(fields.refreshTokenField);
    setRefreshHeadersText(fields.headersText);
    setRefreshCookieName(fields.cookieName);
    syncTokenRefresh({
      method: fields.method,
      url: fields.url,
      refresh_token: fields.token,
      body: fields.body,
      response_sample: fields.sample,
      access_token_field: fields.accessTokenField,
      refresh_token_field: fields.refreshTokenField,
      headers_text: fields.headersText,
      refresh_cookie_name: fields.cookieName,
    });
    if (rotation) syncAdvanced((base) => applyRefreshResult(base, rotation));
    // Access Token 落在站点级 auth_token 上，凭证注入规则决定它怎么带进三处采集请求
    syncAdvanced((base) => ({ ...base, auth_token: fields.authToken.trim() || null, auth_inject: injectRules(fields.inject) }));
    // 认证统一收口：刚换新过 token，各接口里手写的 Authorization 就没有存在意义了，
    // 移除后认证只走站点级一处；留着的话旧值会覆盖站点令牌，换新也追不上
    if (rotation) {
      let removed = false;
      syncAdvanced((base) => {
        const result = stripHardcodedAuth(base);
        removed = removed || result.removed;
        return result.config;
      });
      if (removed) toast("已改为站点统一认证，各接口里写死的 Authorization 已移除");
    }
    setActiveSub(null);
  }

  async function save() {
    if (refreshUrlError || refreshBodyError || refreshSampleError) {
      toast("续签配置里还有标红的填写问题，改好再保存");
      return;
    }
    if (refreshUrl.trim() && !refreshToken.trim()) {
      toast("配了续签接口就要填 Refresh Token，续签全靠它换新凭证");
      return;
    }
    let config = buildConfig();
    if (!config) return;
    // 地址清空 = 移除续签配置（同步时不动 JSON，统一在保存时落地）
    if (!refreshUrl.trim() && config.token_refresh) delete config.token_refresh;
    let note = "";
    // 老配置自动迁移：站点令牌在管认证时，接口 headers 里写死的 Authorization 一律移除，
    // 认证只走站点级一处；留着的话旧值会覆盖站点令牌，续签换新也追不上
    if (typeof config.auth_token === "string" && config.auth_token.trim()) {
      const result = stripHardcodedAuth(config);
      if (result.removed) {
        config = result.config;
        note = "已改为站点统一认证，各接口里写死的 Authorization 已移除";
      }
    }
    // 打开编辑期间后台续签可能已自动跑过、服务端换了新的 refresh_token：用户没动过输入框时以服务端最新值为准
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
              placeholder="留空则按采集地址自动生成，如 example-newapi"
              style={{ width: "min(280px, 100%)" }}
            />
          </SettingRow>
          {/* 认证方式收在「认证与续签」弹窗里，和它控制的输入区在一起；入口卡片标注已配置状态 */}
          <SettingRow label="目标模型" hint="从 models.dev 目录搜索勾选；站点自己的别名或新模型，输入后回车也能加">
            <ModelMultiSelect
              value={models}
              onChange={(next) => {
                setModels(next);
                syncAdvanced((base) => ({ ...base, models: next }));
              }}
            />
          </SettingRow>
          <SettingRow
            label="分组白名单"
            hint="选填：只采这些分组的价格，渠道状态也只查这些分组；从已采集的分组里勾选，也可以直接输入回车添加；留空就全部采集"
          >
            <GroupMultiSelect
              siteId={originalId}
              value={groupsFromText(statusGroupsText)}
              onChange={(next) => commitGroupsText(next.join(", "))}
            />
            {statusGroupsText.trim() && (
              <span style={{ fontSize: 12, color: "var(--tone-yellow-text)" }}>
                保存后，没被选中的分组的价格快照、历史、事件和检测记录会被清掉，且无法恢复
              </span>
            )}
          </SettingRow>

          {/* 入口卡片：点击打开对应子弹窗，确定才写回草稿；价格采集卡片实时显示当前采集地址 */}
          <div style={{ display: "grid", gap: 8, gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))" }}>
            <EntryCard
              title="价格采集"
              desc={`${headless.enabled ? "网页模式·" : ""}${url.trim() ? shortenUrlText(url) : "填价格接口或网页地址"}`}
              configured={Boolean(url.trim() || endpointHeadersText.trim() || headlessConfigured || ratioUrl.trim())}
              onClick={() => setActiveSub("price")}
            />
            <EntryCard
              title="认证与续签"
              desc="站点的认证凭据在这里配，token 失效自动换新"
              configured={Boolean(refreshUrl.trim()) || Boolean(authTokenConfigured)}
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
              desc="公告有变化时记为事件"
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
            authToken,
            method: refreshMethod,
            url: refreshUrl,
            token: refreshToken,
            body: refreshBody,
            sample: refreshSample,
            accessTokenField,
            refreshTokenField,
            headersText: refreshHeadersText,
            cookieName: refreshCookieName,
            inject: injectInitial,
          }}
          priceUrl={url}
          runTest={runRefreshTest}
          onCommit={commitAuth}
          onClose={closeAuth}
        />
      )}
      {activeSub === "status" && (
        <StatusSubModal
          initialUrl={statusUrl}
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

/* ---------- 行内采集异常提示 ---------- */

const HEALTH_SECTIONS: { key: "price" | "status" | "notice"; label: string }[] = [
  { key: "price", label: "价格采集" },
  { key: "status", label: "渠道状态" },
  { key: "notice", label: "站点公告" },
];

/** 站点行内的采集异常小图标：红=采集失败，黄=需认证/没抓到数据；悬停看三类明细，下一轮采集正常自动消失。 */
function SiteHealthTip({ health }: { health: SiteCollectHealth | undefined }) {
  const issues = HEALTH_SECTIONS.map((section) => ({ ...section, issue: health?.[section.key] })).filter(
    (entry): entry is { key: "price" | "status" | "notice"; label: string; issue: SiteCollectIssue } =>
      Boolean(entry.issue),
  );
  if (issues.length === 0) return null;
  const tone = issues.some((entry) => entry.issue.level === "error") ? ("red" as const) : ("yellow" as const);
  return (
    <Tip
      tone={tone}
      ariaLabel="采集异常提示"
      content={
        <span style={{ display: "grid", gap: 8 }}>
          {issues.map(({ key, label, issue }) => (
            <span key={key} style={{ display: "grid", gap: 1 }}>
              <span style={{ display: "inline-flex", alignItems: "baseline", gap: 8 }}>
                <span style={{ fontWeight: 550, color: issue.level === "error" ? "#fca5a5" : "#fcd34d" }}>
                  {label}·{issue.level === "error" ? "错误" : "注意"}
                </span>
                <span style={{ opacity: 0.6, fontSize: 11 }}>{formatTime(issue.time)}</span>
              </span>
              <span style={{ wordBreak: "break-all" }}>{issue.message}</span>
            </span>
          ))}
        </span>
      }
    />
  );
}

/** 站点管理页顶部的配置速查：按场景一句话"怎么配"，再加几条最容易踩的注意事项 */
function SitesGuide() {
  const scenarios: { when: string; how: string }[] = [
    { when: "接口公开，直接能访问", how: "采集方式用「接口直采」，地址填接口即可，不用配认证" },
    {
      when: "接口要令牌",
      how: "认证方式选「固定令牌」，令牌填进「认证与续签」；下面「凭证注入」决定它怎么带进价格、渠道状态、公告三处请求（默认 Authorization: Bearer）",
    },
    {
      when: "令牌很快过期（如 new-api 登录会话）",
      how: "认证方式选「登录会话自动续签」，把浏览器里 new_api_refresh Cookie 的值贴进 Refresh Token 点「测试续签」；Access Token 留空也行，采集被拒时会自动续签补上。贴完回浏览器重新登录一次（两边各用各的会话），每个登录最长保持 30 天",
    },
    {
      when: "直接抓页面是空壳（价格靠 JS 算，如单页应用）",
      how: "先试接口直采，抓不到会自动换无头浏览器重试；确认要渲染就选「网页模式」，Cookie、localStorage 从 F12 → 应用 → Cookies 里抄",
    },
    { when: "渠道状态、站点公告", how: "各自卡片里填地址就行，认证自动沿用站点凭证；被拒时会自动续签一次再试" },
  ];
  const notes = [
    "认证在「认证与续签」里配一处：凭证注入把 token 送到价格、渠道状态、公告三处，接口请求头里手写的认证头会被它盖掉并自动移除",
    "要让某处带 Cookie（如 new_api_refresh），在凭证注入那行把头名填成 cookie、值填成 new_api_refresh=${refresh_token}，续签换新会自动跟着变",
    "localStorage 不会发给服务器，只在页面脚本内部读取；页面的 JS 靠它取登录信息时才需要填",
    "配完点行内的「测试」马上验证；站点行的红/黄小标就是最近一次采集的异常提示",
  ];
  return (
    <div style={{ display: "grid", gap: 12, paddingTop: 10, borderTop: "1px solid var(--border)" }}>
      <div style={{ display: "grid", gap: 6 }}>
        {scenarios.map((item) => (
          <div
            key={item.when}
            style={{ display: "grid", gridTemplateColumns: "minmax(140px, 260px) 1fr", gap: 10, fontSize: 12.5 }}
          >
            <span style={{ color: "var(--text-2)", fontWeight: 550 }}>{item.when}</span>
            <span style={{ color: "var(--text-3)" }}>{item.how}</span>
          </div>
        ))}
      </div>
      <div style={{ display: "grid", gap: 4 }}>
        {notes.map((note) => (
          <span key={note} style={{ fontSize: 12, color: "var(--text-3)" }}>
            · {note}
          </span>
        ))}
      </div>
    </div>
  );
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
      key: "models",
      title: "模型数",
      align: "right",
      // 右对齐的数量列，两位数加边距即够
      width: 84,
      render: (_v, row) => row.models?.length ?? 0,
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
          <span style={{ fontSize: 13, color: "var(--text-2)" }}>各种情况怎么配？接口直采还是无头浏览器、令牌过期怎么办，速查里都有</span>
          <Btn variant="text" size="sm" onClick={() => setGuideOpen((open) => !open)}>
            {guideOpen ? "收起速查" : "展开速查"}
          </Btn>
        </div>
        {guideOpen && <SitesGuide />}
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
