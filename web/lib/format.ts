/** 展示格式化；语义色映射只输出 tone 名，颜色由 CSS 变量（.tone-*）按主题渲染。 */

export type Tone = "green" | "blue" | "yellow" | "red" | "gray";

export const EVENT_META: Record<string, { label: string; tone: Tone }> = {
  new: { label: "新增", tone: "blue" },
  changed: { label: "价格变化", tone: "yellow" },
  price_increased: { label: "涨价", tone: "red" },
  price_decreased: { label: "降价", tone: "green" },
  restored: { label: "恢复", tone: "green" },
  status_changed: { label: "状态变化", tone: "yellow" },
  notice_init: { label: "公告建档", tone: "blue" },
  notice_changed: { label: "公告更新", tone: "yellow" },
};

export function eventMeta(kind: string): { label: string; tone: Tone } {
  return EVENT_META[kind] ?? { label: kind, tone: "gray" };
}

/** 事件流里区分公告事件与价格事件：公告事件没有 model/价格字段，渲染走独立分支。 */
export function isNoticeEvent(event: { kind: string; model?: unknown }): event is import("./types").NoticeEvent {
  return event.kind === "notice_init" || event.kind === "notice_changed";
}

/** 公告正文的单行摘要：去掉 Markdown 标题井号，超长截断；事件流与首页侧栏共用。 */
export function noticeExcerpt(content: string): string {
  const line =
    content
      .split("\n")
      .map((part) => part.trim().replace(/^#+\s*/, ""))
      .filter(Boolean)[0] ?? content;
  return line.length > 120 ? `${line.slice(0, 120)}…` : line;
}

export function formatPrice(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const digits = value >= 100 ? 1 : value >= 1 ? 2 : 3;
  let text = value.toFixed(digits);
  if (text.includes(".")) text = text.replace(/0+$/, "").replace(/\.$/, "");
  return text;
}

/** 渠道免费档：输入输出标价都为 0（如 OpenRouter 的 :free 模型）。 */
export function isFreePrice(list?: { input?: number | null; output?: number | null } | null): boolean {
  return list?.input === 0 && list?.output === 0;
}

/** 由 unit（如 "CNY/1M tokens"）解析货币符号；未知币种返回空串，单位交给表头表达。 */
export function currencySymbol(unit: string | null | undefined): string {
  const code = unit?.split("/")[0]?.trim().toUpperCase();
  if (code === "CNY") return "¥";
  if (code === "USD") return "$";
  return "";
}

/** 阶梯计价格的悬停解释；格内逐档列出单价的格子和单行摘要都挂这条。 */
export const TIERED_PRICE_TIP =
  "阶梯计价：该站点按单次请求的上下文长度分档计价，此处逐档列出各档单价。";

/** token 数缩写：272000 → 272K、1000000 → 1M，不足 1K 原样。 */
export function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${Math.round((value / 1_000_000) * 10) / 10}M`;
  if (value >= 1000) return `${Math.round(value / 1000)}K`;
  return String(value);
}

/** 档位标注：上限档 "≤272K"、无上限档 ">272K"、两端都有 "min–max"；无边界信息为 null。 */
function tierLabel(tier: { context_min?: unknown; context_max?: unknown }): string | null {
  const min = typeof tier.context_min === "number" ? tier.context_min : null;
  const max = typeof tier.context_max === "number" ? tier.context_max : null;
  if (max != null && (min == null || min <= 0)) return `≤${formatTokens(max)}`;
  if (min != null && max == null) return `>${formatTokens(min)}`;
  if (min != null && max != null) return `${formatTokens(min)}–${formatTokens(max)}`;
  return null;
}

export interface TierPrice {
  label: string | null;
  price: number;
}

/** 某价格字段的阶梯档单价（label 已格式化）；顶层已有价或非阶梯计价返回 []。 */
export function tieredPrices(
  row:
    | {
        input_price?: number | null;
        output_price?: number | null;
        metadata?: { pricing_rules?: unknown; [key: string]: unknown } | null;
      }
    | null
    | undefined,
  field: "input_price" | "output_price",
): TierPrice[] {
  if (row?.[field] != null) return [];
  const rules = row?.metadata?.pricing_rules as { groups?: { tiers?: Record<string, unknown>[] }[] } | undefined;
  const result: TierPrice[] = [];
  for (const tier of (rules?.groups ?? []).flatMap((group) => group.tiers ?? [])) {
    const price = tier[field];
    if (typeof price !== "number") continue;
    result.push({ label: tierLabel(tier), price });
  }
  return result;
}

/** 排序与比较用的有效价：顶层价优先，否则取阶梯最低档价。 */
export function effectivePrice(
  row:
    | {
        input_price?: number | null;
        output_price?: number | null;
        metadata?: { pricing_rules?: unknown; [key: string]: unknown } | null;
      }
    | null
    | undefined,
  field: "input_price" | "output_price",
): number | null {
  if (row?.[field] != null) return row[field];
  const prices = tieredPrices(row, field).map((tier) => tier.price);
  return prices.length ? Math.min(...prices) : null;
}

/** 站点价统一按 RMB 展示：USD 价乘快照汇率折算，CNY 原样；无法折算（无汇率/未知币种）返回 null。 */
export function toCnyPrice(
  value: number | null | undefined,
  unit: string | null | undefined,
  rate: number | null | undefined,
): number | null {
  if (typeof value !== "number") return null;
  const code = unit?.split("/")[0]?.trim().toUpperCase();
  if (code === "CNY") return value;
  if (code === "USD" && typeof rate === "number" && rate > 0) return value * rate;
  return null;
}

/** 折算成 RMB 的有效价（排序用）：换算优先，汇率缺失时回落原始有效价，避免整列不可排序。 */
export function effectiveCnyPrice(
  row:
    | {
        input_price?: number | null;
        output_price?: number | null;
        unit?: string;
        metadata?: { pricing_rules?: unknown; [key: string]: unknown } | null;
      }
    | null
    | undefined,
  field: "input_price" | "output_price",
  rate: number | null | undefined,
): number | null {
  const raw = effectivePrice(row, field);
  const converted = toCnyPrice(raw, row?.unit, rate);
  return converted ?? raw;
}

/** 单行阶梯价文本（事件流摘要用）："≤272K ¥1 / >272K ¥2"，单档只显示价格；非阶梯返回 null。
 *  传 rate 时统一折算成 RMB；无法折算回落原币符号。 */
export function tieredPriceText(
  row:
    | {
        unit?: string;
        input_price?: number | null;
        output_price?: number | null;
        metadata?: { pricing_rules?: unknown; [key: string]: unknown } | null;
      }
    | null
    | undefined,
  field: "input_price" | "output_price",
  rate?: number | null,
): string | null {
  const tiers = tieredPrices(row, field);
  if (tiers.length === 0) return null;
  const converted = tiers.every((tier) => toCnyPrice(tier.price, row?.unit, rate) !== null);
  const symbol = converted ? "¥" : currencySymbol(row?.unit);
  return tiers
    .map((tier) => {
      const price = converted ? toCnyPrice(tier.price, row?.unit, rate) : tier.price;
      return `${tier.label ? `${tier.label} ` : ""}${symbol}${formatPrice(price)}`;
    })
    .join(" / ");
}

/** 本地时区的日期 key（YYYY-MM-DD）；图表按天分桶必须用本地日期，UTC 会让东八区 0–8 点的记录归错天。 */
export function dayKey(ts: number): string {
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function formatTime(ts: number | undefined | null): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${dayKey(ts)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 折扣 = 站点价 / 厂商价；0.21 显示为 21%，越小越便宜。 */
export function formatDiscount(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value * 100)}%`;
}

export function discountTone(value: number | null | undefined): Tone {
  if (value === null || value === undefined) return "gray";
  if (value <= 0.5) return "green";
  if (value <= 0.8) return "yellow";
  return "red";
}

/** 采集状态语义：key 与后端 price_status / collect_status 文档对齐。 */
export const STATUS_META: Record<string, { label: string; tone: Tone }> = {
  ok: { label: "正常", tone: "green" },
  confirmed: { label: "正常", tone: "green" },
  rule_only: { label: "规则价", tone: "blue" },
  candidate: { label: "待确认", tone: "blue" },
  auth_required: { label: "需认证", tone: "yellow" },
  unavailable: { label: "无数据", tone: "gray" },
  disabled: { label: "已停用", tone: "gray" },
  error: { label: "采集失败", tone: "red" },
};

export function statusMeta(key: string): { label: string; tone: Tone } {
  return STATUS_META[key] ?? { label: key, tone: "gray" };
}

/** 价格记录的状态 key：认证缺失优先于 price_status 展示。 */
export function recordStatusKey(row: { price_status: string; requires_auth: boolean }): string {
  return row.requires_auth ? "auth_required" : row.price_status;
}

/** 记录层面的失败原因：优先顶层 status_reason，回落到 metadata.error / metadata.notes。 */
export function rowReason(row: {
  status_reason?: string | null;
  metadata?: { error?: unknown; notes?: unknown } | null;
}): string | null {
  const fromMeta = row.metadata?.error ?? row.metadata?.notes;
  return row.status_reason ?? (typeof fromMeta === "string" && fromMeta ? fromMeta : null);
}
