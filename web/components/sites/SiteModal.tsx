"use client";

import { useMemo, useState } from "react";
import { toast, Btn, Input, Modal } from "../ui";
import { apiSend } from "@/lib/api";
import { GroupMultiSelect } from "./MultiSelect";
import { advancedJsonError, originOf, textToDict } from "./siteShared";
import { SettingRow } from "../ui";
import { errorText } from "@/lib/api";
import { INJECT_TARGETS, type AuthFields, type InjectRule, type InjectTarget, type RefreshOverride, type RefreshTestResult } from "./siteAuth";
import { EntryCard, JsonSubModal, NoticeSubModal, PriceSubModal, StatusSubModal, type HeadlessForm, type PriceFields } from "./siteModalParts";
import { AuthSubModal } from "./SiteAuthModal";
import type { SiteConfig } from "@/lib/types";

/* ---------- 主弹窗 ---------- */

/** 表单状态快照：保存与实时同步都基于它，保证两条路径语义一致。 */
type SiteFormState = {
  id: string;
  url: string;
  statusUrl: string;
  statusGroupsText: string;
  noticeUrl: string;
  endpointHeadersText: string;
};

/** ratio_url 两种形态（纯字符串 / {url, headers} 对象）→ 表单用的地址与请求头文本 */
function ratioFromConfig(network: SiteConfig["network"]): { url: string; headersText: string } {
  const ratio = network?.ratio_url;
  if (typeof ratio === "string") return { url: ratio, headersText: "" };
  if (ratio !== null && typeof ratio === "object") {
    return { url: typeof ratio.url === "string" ? ratio.url : "", headersText: dictToText(ratio.headers) };
  }
  return { url: "", headersText: "" };
}

/** 键值对象 → "Key: Value" 多行文本。 */
function dictToText(dict: Record<string, string> | null | undefined): string {
  return Object.entries(dict ?? {})
    .map(([key, value]) => `${key}: ${value}`)
    .join("\n");
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

/** 站点配置 → 凭证注入规则初值，展示与后端 auth_inject_headers 的实际注入行为对齐：
 *  配过 auth_inject（哪怕个别目标被清空）就只按配置回显，没配的目标显示为空——后端对它不会注入；
 *  完全没配过才按老行为回显（Authorization: Bearer <access token>），且站点真有 token 才回显。
 *  没配认证的站点什么都不注入，也就不显示规则，免得看到一条永远发不出去的头。 */
function injectFromConfig(
  config: Pick<SiteConfig, "auth_token" | "auth_header" | "auth_prefix" | "auth_inject">,
): Record<InjectTarget, InjectRule> {
  const hasToken = typeof config.auth_token === "string" && config.auth_token.trim() !== "";
  const hasInjectRules =
    config.auth_inject !== null && typeof config.auth_inject === "object" && Object.keys(config.auth_inject).length > 0;
  const legacy: InjectRule | null =
    hasToken && !hasInjectRules
      ? {
          header: typeof config.auth_header === "string" && config.auth_header.trim() ? config.auth_header.trim() : "Authorization",
          value: `${typeof config.auth_prefix === "string" ? config.auth_prefix : "Bearer "}\${access_token}`,
        }
      : null;
  const none: InjectRule = { header: "", value: "" };
  return Object.fromEntries(
    INJECT_TARGETS.map((target) => {
      const rule = config.auth_inject?.[target];
      const header = typeof rule?.header === "string" ? rule.header : "";
      const value = typeof rule?.value === "string" ? rule.value : "";
      return [target, header.trim() && value.trim() ? { header, value } : (legacy ?? none)];
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

/** 站点配置 → Headless表单初值 */
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

/** 子弹窗标识：主弹窗同一时间最多打开一个 */
type SubKey = "price" | "auth" | "status" | "notice" | "json";

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

/** 表单已覆盖的顶层字段：之外的进「仅在 JSON 里维护」清单，让"配了但表单没输入框"的配置在界面上看得见 */
const FORM_COVERED_KEYS = new Set([
  "id",
  "adapter",
  "enabled",
  "network",
  "status",
  "notice",
  "auth_token",
  "auth_header",
  "auth_prefix",
  "token_refresh",
  "auth_inject",
  "headless",
]);

/** 「仅在 JSON 里维护」摘要：只描述形态不展开值（认证串敏感），对象列一层键名 */
function jsonOnlySummary(value: unknown): string {
  if (typeof value === "string") return `文本 ${value.length} 字`;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return `${value.length} 项`;
  if (value !== null && typeof value === "object") {
    const keys = Object.keys(value as Record<string, unknown>);
    if (keys.length === 0) return "空对象";
    const shown = keys.slice(0, 4).join("、");
    return keys.length > 4 ? `${shown} 等 ${keys.length} 项` : shown;
  }
  return "已配置";
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
  // 网页模式·Headless表单草稿
  const [headless, setHeadless] = useState<HeadlessForm>(() => headlessFromConfig(initial));
  const [activeSub, setActiveSub] = useState<SubKey | null>(null);
  const [advanced, setAdvanced] = useState(JSON.stringify(initial, null, 2));
  const [saving, setSaving] = useState(false);
  const advancedError = advancedJsonError(advanced);
  // JSON 里有、表单没输入框的字段：在主弹窗亮出来，免得"配了但界面上看不见"
  const jsonOnlyEntries = useMemo<[string, unknown][]>(() => {
    if (advancedError) return [];
    try {
      const parsed = JSON.parse(advanced) as Record<string, unknown>;
      return Object.entries(parsed).filter(([key]) => !FORM_COVERED_KEYS.has(key));
    } catch {
      return [];
    }
  }, [advanced, advancedError]);
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
    return {
      price: sectionKeys("network"),
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
    return { id, url, statusUrl, statusGroupsText, noticeUrl, endpointHeadersText };
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

  // 价格采集子弹窗确定：价格接口（地址/请求头/Headless）与倍率接口地址一并写回草稿；
  // 倍率专用请求头不在表单里编辑，按当前草稿原样带回（高级 JSON 改过会经 configToForm 同步进来）
  function commitPrice(next: PriceFields) {
    setUrl(next.url);
    setEndpointHeadersText(next.headersText);
    setHeadless(next.headless);
    setRatioUrl(next.ratioUrl);
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
      const ratioHeaders = textToDict(ratioHeadersText);
      if (ratioTrimmed) {
        network.ratio_url =
          Object.keys(ratioHeaders).length > 0 ? { url: ratioTrimmed, headers: ratioHeaders } : ratioTrimmed;
      } else {
        delete network.ratio_url;
      }
      // Headless：开关关且内容全空时移除该字段
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
    headless.enabled || headless.cookies.length > 0 || headless.localStorage.length > 0;

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
              desc={jsonOnlyEntries.length > 0 ? `${jsonOnlyEntries.length} 个字段只在 JSON 里维护` : "直接编辑整份站点配置"}
              configured={jsonOnlyEntries.length > 0}
              onClick={() => setActiveSub("json")}
            />
          </div>
          {jsonOnlyEntries.length > 0 && (
            <div style={{ display: "grid", gap: 4 }}>
              <span style={{ fontSize: 13.5 }}>仅在 JSON 里维护</span>
              {jsonOnlyEntries.map(([key, value]) => (
                <div key={key} style={{ fontSize: 12.5 }}>
                  <span className="mono" style={{ fontWeight: 550 }}>
                    {key}
                  </span>
                  <span style={{ color: "var(--text-3)" }}>：{jsonOnlySummary(value)}</span>
                </div>
              ))}
              <span style={{ fontSize: 12, color: "var(--text-3)" }}>
                这些配置没有表单输入框，点「高级 JSON」卡片查看与编辑；表单保存不会动它们。
              </span>
            </div>
          )}
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
          }}
          warningKeys={headerOverrideWarnings.price}
          injected={injectInitial.price}
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
          injected={injectInitial.status}
          onCommit={commitStatus}
          onClose={() => setActiveSub(null)}
        />
      )}
      {activeSub === "notice" && (
        <NoticeSubModal
          initialUrl={noticeUrl}
          initialHeadersText={noticeHeadersText}
          warningKeys={headerOverrideWarnings.notice}
          injected={injectInitial.notice}
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

export { SiteModal };
