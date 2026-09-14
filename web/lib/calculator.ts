/** 花费计算：单价（每 100 万 token）× 用量 → 各档小计与总价。
 *  与 React 无关的纯函数：便于单测，也供 URL 编解码复用。文案不在此文件，统一放 copy.ts。 */

/** 四个计费档（与站点价目卡一致）。缓存存储价只展示不进总价——没有存储量数据。 */
export type PriceKey = "input" | "output" | "cacheRead" | "cacheWrite";

export type CalcPrices = Record<PriceKey, number | null>;

export interface CostLine {
  key: PriceKey;
  /** 单价（与 CalcPrices 同币种，每 100 万 token） */
  unitPrice: number;
  /** 该档计费的 token 数 */
  tokens: number;
  subtotal: number;
  /** 占总价比例 0–1；总价为 0 时为 0 */
  share: number;
}

export interface CalcResult {
  lines: CostLine[];
  total: number;
}

export const PRICE_KEYS: PriceKey[] = ["input", "output", "cacheRead", "cacheWrite"];

/** 单价口径：目录与站点价统一按每 100 万 token 标价。 */
const TOKENS_PER_UNIT = 1_000_000;

/** 缓存命中率默认 98%：反复带同前缀的调用（agent、多轮对话）普遍在这个量级。 */
export const DEFAULT_HIT_RATE = 98;

/** 总用量快捷档位（与 copy.ts 的 tokenPresets 标签按序对应），默认 1 亿。 */
export const TOKEN_PRESET_VALUES = [10_000_000, 100_000_000, 1_000_000_000] as const;
export const DEFAULT_TOTAL_TOKENS: number = TOKEN_PRESET_VALUES[1];

/** 输入占 99.2%：agent 类实际用量口径，成本几乎由输入决定。 */
export const INPUT_SHARE = 0.992;

/** 用量口径：一个总量（快捷档位）+ 缓存命中率，不按计费档分别填。 */
export interface CalcUsage {
  total: number | null;
  hitRate: number | null;
}

function finiteOrNull(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function positiveTokens(value: number | null | undefined): number {
  const num = finiteOrNull(value);
  return num !== null && num > 0 ? num : 0;
}

/** 命中率：空/非法回落默认值，越界钳到 0–100。 */
export function parseHitRate(text: string): number {
  const num = parseAmount(text);
  if (num === null) return DEFAULT_HIT_RATE;
  return Math.min(100, Math.max(0, num));
}

/** 总花费 = 缓存命中部分×命中价 + 未命中输入×输入价 + 输出×输出价。
 *  输入按命中率切成命中/未命中两段（不另计缓存存储）；某一档没单价就整段不计，不编数。 */
export function calcCost(prices: CalcPrices, usage: CalcUsage): CalcResult {
  const hit = parseHitRate(usage.hitRate != null ? String(usage.hitRate) : "") / 100;
  const totalTokens = positiveTokens(usage.total);
  const inputTokens = totalTokens * INPUT_SHARE;
  const outputTokens = totalTokens - inputTokens;
  const buckets: { key: PriceKey; tokens: number; price: number | null }[] = [
    { key: "cacheRead", tokens: inputTokens * hit, price: prices.cacheRead },
    { key: "input", tokens: inputTokens * (1 - hit), price: prices.input },
    { key: "output", tokens: outputTokens, price: prices.output },
  ];
  const lines: CostLine[] = [];
  for (const bucket of buckets) {
    const price = bucket.price;
    if (price === null || price < 0) continue;
    const tokens = bucket.tokens;
    if (tokens <= 0) continue;
    lines.push({
      key: bucket.key,
      unitPrice: price,
      tokens,
      subtotal: (tokens / TOKENS_PER_UNIT) * price,
      share: 0,
    });
  }
  const total = lines.reduce((sum, line) => sum + line.subtotal, 0);
  if (total > 0) {
    for (const line of lines) line.share = line.subtotal / total;
  }
  return { lines, total };
}

/** 金额显示：大额少留位、小额多留位，避免 0.0004 被四舍五入成 0。
 *  ≥1 两位、≥0.01 四位、其余六位；整数与 0 原样。 */
export function formatAmount(value: number | null | undefined): string {
  const num = finiteOrNull(value);
  if (num === null) return "—";
  if (num === 0) return "0";
  const digits = Math.abs(num) >= 1 ? 2 : Math.abs(num) >= 0.01 ? 4 : 6;
  return num
    .toFixed(digits)
    .replace(/(\.\d*?)0+$/, "$1")
    .replace(/\.$/, "");
}

/** 金额输入解析：空串与非法值一律为 null（留空表示该档不计入），负数按 0 处理。 */
export function parseAmount(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const num = Number(trimmed);
  if (!Number.isFinite(num) || num < 0) return null;
  return num;
}

/** URL 里的总量只认快捷档位，其余一律回落默认档。 */
function presetTotal(value: number | null): number {
  return value !== null && (TOKEN_PRESET_VALUES as readonly number[]).includes(value)
    ? value
    : DEFAULT_TOTAL_TOKENS;
}

export type CalcSource = "official" | "site";

export interface CalcState {
  source: CalcSource;
  model: string;
  site: string;
  /** 单价币种："USD" 走汇率折算，"CNY" 原样；未知币种只显示原币 */
  currency: string;
  prices: CalcPrices;
  /** 总 token 用量；缺省回落默认档（1 亿） */
  totalTokens: number | null;
  /** 缓存命中率 0–100；缺省回落默认值（98） */
  hitRate: number | null;
}

export const EMPTY_PRICES: CalcPrices = {
  input: null,
  output: null,
  cacheRead: null,
  cacheWrite: null,
};

export const EMPTY_STATE: CalcState = {
  source: "official",
  model: "",
  site: "",
  currency: "USD",
  prices: EMPTY_PRICES,
  totalTokens: DEFAULT_TOTAL_TOKENS,
  hitRate: DEFAULT_HIT_RATE,
};

/** URL 参数名：短键，保证分享链接不至于过长。 */
const PARAM = {
  source: "src",
  model: "m",
  site: "s",
  currency: "cur",
  price: { input: "pi", output: "po", cacheRead: "pr", cacheWrite: "pw" },
  total: "t",
  hit: "h",
} as const;

function compact(value: number | null): string | undefined {
  return value === null ? undefined : String(value);
}

/** 状态 → query 参数；空值与默认值不写，保证链接干净。 */
export function encodeCalcState(state: CalcState): URLSearchParams {
  const params = new URLSearchParams();
  if (state.source === "site") params.set(PARAM.source, "site");
  if (state.model) params.set(PARAM.model, state.model);
  if (state.site) params.set(PARAM.site, state.site);
  // USD 是解码默认值，省掉无损；其余币种必须写入，否则还原时币种会变
  if (state.currency && state.currency !== "USD") params.set(PARAM.currency, state.currency);
  for (const key of PRICE_KEYS) {
    const value = compact(state.prices[key]);
    if (value !== undefined) params.set(PARAM.price[key], value);
  }
  if (state.totalTokens != null && state.totalTokens !== DEFAULT_TOTAL_TOKENS) {
    params.set(PARAM.total, String(state.totalTokens));
  }
  if (state.hitRate != null && state.hitRate !== DEFAULT_HIT_RATE) {
    params.set(PARAM.hit, String(state.hitRate));
  }
  return params;
}

/** query 参数 → 状态；总量只认快捷档位，命中率缺失回落默认值。 */
export function decodeCalcState(params: URLSearchParams): CalcState {
  const prices = { ...EMPTY_PRICES };
  for (const key of PRICE_KEYS) {
    prices[key] = parseAmount(params.get(PARAM.price[key]) ?? "");
  }
  return {
    source: params.get(PARAM.source) === "site" ? "site" : "official",
    model: params.get(PARAM.model) ?? "",
    site: params.get(PARAM.site) ?? "",
    currency: (params.get(PARAM.currency) ?? "USD").toUpperCase(),
    prices,
    totalTokens: presetTotal(parseAmount(params.get(PARAM.total) ?? "")),
    hitRate: parseHitRate(params.get(PARAM.hit) ?? ""),
  };
}
