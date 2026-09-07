/**
 * 渠道状态提取：各站点的状态接口返回自由结构 JSON（status.py 不做归一化），
 * 这里按启发式找出「渠道名 + 状态」条目，供总览点阵与详情页共用；
 * 渠道自带的时间线（timeline/history 等）会并成该渠道的检测点序列。
 */

export interface ChannelStatus {
  name: string;
  status: string;
  ok: boolean;
  provider?: string;
  model?: string;
  /** 站点自报的 7 日可用率（0–100） */
  availability7d?: number;
}

/** 单个检测点：状态 + 检测时间（秒级时间戳，站点未提供时间时缺省）+ 自报延迟（ms）。 */
export interface ChannelDot {
  status: string;
  ok: boolean;
  at?: number;
  latency?: number;
}

/** 一个渠道的检测点序列（时间升序）。 */
export interface ChannelDotRow extends ChannelStatus {
  dots: ChannelDot[];
}

/** 点阵行名归一：去首尾空格、忽略大小写，用于与计价分组名匹配。 */
const normalizeChannelName = (name: string) => name.trim().toLowerCase();

/**
 * 按目标分组过滤点阵行（总览渠道列用）：只要有一行名字对得上目标分组，
 * 就只显示匹配的行；目标分组为空或一行都对不上（渠道形态接口、站点无分组概念）时不过滤。
 * 渠道状态详情页展示全量，不走这里。
 */
export function filterDotsByGroups(dots: ChannelDotRow[], groups: ReadonlySet<string>): ChannelDotRow[] {
  if (groups.size === 0) return dots;
  const targets = new Set(Array.from(groups, normalizeChannelName));
  const matched = dots.filter((row) => targets.has(normalizeChannelName(row.name)));
  return matched.length > 0 ? matched : dots;
}

/** 成功率窗口：取每个渠道最近 15 次检测，与总览渠道点阵展示的列数一致。 */
const RECENT_DOT_WINDOW = 15;

/** 行的平均渠道成功率：每个渠道取最近检测的正常占比，再对各渠道求平均（0–1）；
 *  无检测数据返回 null，由调用方按缺数据处理。 */
export function channelSuccessRate(dots: ChannelDotRow[] | undefined): number | null {
  if (!dots || dots.length === 0) return null;
  let sum = 0;
  let count = 0;
  for (const row of dots) {
    const recent = row.dots.slice(-RECENT_DOT_WINDOW);
    if (recent.length === 0) continue;
    sum += recent.filter((dot) => dot.ok).length / recent.length;
    count += 1;
  }
  return count > 0 ? sum / count : null;
}

/** 判定为“正常”的状态词表；其余取值一律按异常/未知渲染灰色。 */
const UP_WORDS = new Set([
  "up",
  "ok",
  "okay",
  "normal",
  "available",
  "online",
  "healthy",
  "operational",
  "active",
  "success",
  "true",
  "1",
  "正常",
  "可用",
  "在线",
  "运行中",
  "健康",
]);

const STATUS_KEYS = ["status", "state", "health"];
// "key"：分组形态接口的分组名（如 groups[].key）
const NAME_KEYS = ["name", "channel", "model", "id", "title", "key"];
/** 纯容器键名：可以递归进入，但不能当作渠道名回退 */
const FALLBACK_NAME_KEYS = new Set(["items", "channels", "data", "list", "models", "services", "result", "results"]);
/** 历史序列键名：不作为独立渠道递归，而是并成所属渠道的检测点 */
const TIMELINE_KEYS = new Set(["timeline", "history", "checks", "events", "logs", "log", "uptime"]);
const TIME_KEYS = ["checked_at", "checkedAt", "checked", "timestamp", "detected_at", "time", "at", "ts"];
const MAX_CHANNELS = 64;
const MAX_DOTS = 60;

function isUp(raw: string): boolean {
  return UP_WORDS.has(raw.trim().toLowerCase());
}

function isScalarish(value: unknown): boolean {
  return value != null && typeof value !== "object";
}

/** 渠道状态键：精确的 status/state/health 优先，其次 *status 后缀（如 primary_status），排除 status_code。 */
function findStatusKey(obj: Record<string, unknown>): string | undefined {
  const exact = STATUS_KEYS.find((key) => isScalarish(obj[key]));
  if (exact) return exact;
  return Object.keys(obj).find((key) => {
    const lower = key.toLowerCase();
    return lower.endsWith("status") && !lower.endsWith("status_code") && isScalarish(obj[key]);
  });
}

function parseTime(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value > 1e12 ? Math.round(value / 1000) : value;
  }
  if (typeof value === "string") {
    const parsed = Date.parse(value);
    return Number.isNaN(parsed) ? undefined : Math.round(parsed / 1000);
  }
  return undefined;
}

/** 依次取第一个有限数值，用于同义字段兜底（如 availability_7d → availability）。 */
function numberOr(...values: unknown[]): number | undefined {
  for (const value of values) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return undefined;
}

/** 可用率统一成 0–100 百分比；部分站点给 0–1 小数。 */
function availabilityPct(...values: unknown[]): number | undefined {
  const value = numberOr(...values);
  return value != null && value <= 1 ? Math.round(value * 10000) / 100 : value;
}

const LATENCY_KEYS = ["latency_ms", "latency", "response_ms", "primary_latency_ms", "average_latency_ms"];

/** 渠道自报延迟（ms）：时间线条目用 latency_ms，渠道条目用 primary_latency_ms，聚合均值用 average_latency_ms。 */
function findLatency(obj: Record<string, unknown>): number | undefined {
  for (const key of LATENCY_KEYS) {
    const value = obj[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return undefined;
}

interface RawChannel extends ChannelStatus {
  dots: ChannelDot[];
}

/** 解析时间线数组为检测点。 */
function timelineDots(node: unknown): ChannelDot[] {
  if (!Array.isArray(node)) return [];
  const dots: ChannelDot[] = [];
  for (const item of node) {
    if (item === null || typeof item !== "object") continue;
    const obj = item as Record<string, unknown>;
    const statusKey = findStatusKey(obj);
    if (!statusKey) continue;
    const status = String(obj[statusKey]);
    dots.push({ status, ok: isUp(status), at: parseTime(TIME_KEYS.map((key) => obj[key]).find((v) => v != null)), latency: findLatency(obj) });
  }
  return dots;
}

function collectChannels(node: unknown, fallback: string | null, depth: number, out: RawChannel[]) {
  if (out.length >= MAX_CHANNELS) return;
  if (Array.isArray(node)) {
    node.forEach((item, index) => collectChannels(item, fallback ?? `#${index + 1}`, depth, out));
    return;
  }
  if (node === null || typeof node !== "object") return;
  const obj = node as Record<string, unknown>;
  const statusKey = findStatusKey(obj);
  let own: RawChannel | null = null;
  if (statusKey) {
    const nameKey = NAME_KEYS.find((key) => typeof obj[key] === "string" || typeof obj[key] === "number");
    const status = String(obj[statusKey]);
    own = {
      name: String(nameKey ? obj[nameKey] : (fallback ?? "渠道")),
      status,
      ok: isUp(status),
      provider: typeof obj.provider === "string" ? obj.provider : undefined,
      model: typeof obj.primary_model === "string" ? obj.primary_model : typeof obj.model === "string" ? obj.model : undefined,
      availability7d: availabilityPct(obj.availability_7d, obj.availability),
      dots: [{ status, ok: isUp(status), latency: findLatency(obj) }],
    };
    out.push(own);
  }
  if (depth > 0) {
    for (const [key, child] of Object.entries(obj)) {
      if (child === null || typeof child !== "object") continue;
      if (TIMELINE_KEYS.has(key.toLowerCase())) {
        if (own) own.dots.push(...timelineDots(child));
        continue;
      }
      collectChannels(child, FALLBACK_NAME_KEYS.has(key) ? null : key, depth - 1, out);
    }
  }
}

/** 从自由结构状态 JSON 提取渠道列表（不含时间线明细）。 */
export function extractChannels(data: unknown): ChannelStatus[] {
  const out: RawChannel[] = [];
  collectChannels(data, null, 3, out);
  return out.map(({ name, status, ok }) => ({ name, status, ok }));
}

/** 同一时间点只保留最早出现的一条，避免多轮采集重复并入同一段内嵌时间线。 */
function dedupeDots(dots: ChannelDot[]): ChannelDot[] {
  const seenAt = new Set<number>();
  const out: ChannelDot[] = [];
  for (const dot of dots) {
    if (dot.at != null) {
      if (seenAt.has(dot.at)) continue;
      seenAt.add(dot.at);
    }
    out.push(dot);
  }
  return out;
}

/**
 * 按站点把状态时序合并成「渠道 → 检测点序列」：渠道快照里自带时间线时直接采用
 * （逐分钟，密度高），否则每次采集记一个点；异常渠道排在前面。
 */
export function channelDotsBySite(records: { site_id: string; captured_at: number; data: unknown }[]): Record<string, ChannelDotRow[]> {
  const maps: Record<string, Map<string, ChannelDot[]>> = {};
  const metas: Record<string, Map<string, RawChannel>> = {};
  for (const record of [...records].sort((a, b) => a.captured_at - b.captured_at)) {
    const raw: RawChannel[] = [];
    collectChannels(record.data, null, 3, raw);
    const byName = new Map<string, RawChannel[]>();
    for (const channel of raw) {
      const list = byName.get(channel.name);
      if (list) list.push(channel);
      else byName.set(channel.name, [channel]);
    }
    const siteMap = (maps[record.site_id] ??= new Map());
    const siteMeta = (metas[record.site_id] ??= new Map());
    for (const [name, instances] of byName) {
      const dots = siteMap.get(name) ?? [];
      const embedded = instances.find((channel) => channel.dots.length > 1);
      if (embedded) {
        dots.push(...embedded.dots);
      } else {
        const first = instances[0];
        dots.push({ status: first.status, ok: first.ok, at: record.captured_at, latency: first.dots[0]?.latency });
      }
      siteMap.set(name, dots);
      if (!siteMeta.has(name)) siteMeta.set(name, instances[0]);
    }
  }
  const result: Record<string, ChannelDotRow[]> = {};
  for (const [site, map] of Object.entries(maps)) {
    const rows = [...map.entries()].map(([name, dots]) => {
      const first = metas[site].get(name);
      const sorted = dedupeDots(dots)
        .sort((a, b) => (a.at ?? 0) - (b.at ?? 0))
        .slice(-MAX_DOTS);
      return {
        name,
        status: first?.status ?? sorted[sorted.length - 1]?.status ?? "unknown",
        ok: sorted[sorted.length - 1]?.ok ?? false,
        provider: first?.provider,
        model: first?.model,
        availability7d: first?.availability7d,
        dots: sorted,
      };
    });
    rows.sort((a, b) => Number(a.ok) - Number(b.ok));
    result[site] = rows;
  }
  return result;
}

/** 可用率趋势上的一个点：时刻 at（秒）时各渠道的正常占比与异常名单。 */
export interface AvailabilityPoint {
  at: number;
  pct: number;
  down: string[];
}

/** 可用率三档：<80% 大面积异常、80–95% 有渠道异常、≥95% 正常。 */
export type RateLevel = "ok" | "warn" | "down";

/** 按当次正常渠道占比归档；阈值与趋势图分段着色、KPI 数字着色共用。 */
export function rateLevel(pct: number): RateLevel {
  if (pct >= 95) return "ok";
  if (pct >= 80) return "warn";
  return "down";
}

/** 异常渠道名单文案：最多点名 6 个，更多时截断并标注总数。趋势图与时段条悬停共用。 */
export function downNamesLabel(down: string[]): string {
  if (down.length === 0) return "";
  const shown = down.slice(0, 6).join("、");
  return down.length > 6 ? `${shown} 等 ${down.length} 个渠道` : shown;
}

/** 自报延迟档位：≥3s 明显慢（红）、≥1s 偏慢（黄）、其余正常。与 KPI、渠道行的着色共用。 */
export function latencyLevel(ms: number): RateLevel {
  if (ms >= 3000) return "down";
  if (ms >= 1000) return "warn";
  return "ok";
}

/**
 * 按站点把渠道检测点合并成可用率时间序列：每个检测时刻取各渠道当时的最新状态，
 * 站点自带逐分钟时间线时密度就是分钟级。
 */
export function availabilityBySite(records: { site_id: string; captured_at: number; data: unknown }[]): Record<string, AvailabilityPoint[]> {
  const bySite = channelDotsBySite(records);
  const result: Record<string, AvailabilityPoint[]> = {};
  for (const [site, rows] of Object.entries(bySite)) {
    const times = new Set<number>();
    for (const row of rows) for (const dot of row.dots) if (dot.at != null) times.add(dot.at);
    const sorted = [...times].sort((a, b) => a - b).slice(-120);
    const series: AvailabilityPoint[] = [];
    for (const at of sorted) {
      let ok = 0;
      let total = 0;
      const down: string[] = [];
      for (const row of rows) {
        let latest: ChannelDot | null = null;
        for (const dot of row.dots) {
          if (dot.at == null) continue;
          if (dot.at <= at) latest = dot;
          else break;
        }
        if (!latest) continue;
        total += 1;
        if (latest.ok) ok += 1;
        else down.push(row.name);
      }
      if (total > 0) series.push({ at, pct: Math.round((ok / total) * 100), down });
    }
    result[site] = series;
  }
  return result;
}

/** 延迟趋势上的一个点：时刻 at（秒）时各渠道的自报延迟（ms，缺席的渠道不在 values 里）。 */
export interface LatencyPoint {
  at: number;
  values: Record<string, number>;
}

/** 按站点把带延迟的检测点合并成「时刻 → 各渠道延迟」序列，供延迟趋势图使用。 */
export function latencyBySite(records: { site_id: string; captured_at: number; data: unknown }[]): Record<string, LatencyPoint[]> {
  const bySite = channelDotsBySite(records);
  const result: Record<string, LatencyPoint[]> = {};
  for (const [site, rows] of Object.entries(bySite)) {
    const withLatency = rows.filter((row) => row.dots.some((dot) => dot.latency != null));
    if (withLatency.length === 0) continue;
    const times = new Set<number>();
    for (const row of withLatency) for (const dot of row.dots) if (dot.at != null) times.add(dot.at);
    const sorted = [...times].sort((a, b) => a - b).slice(-120);
    const series: LatencyPoint[] = [];
    for (const at of sorted) {
      const values: Record<string, number> = {};
      for (const row of withLatency) {
        let latest: ChannelDot | null = null;
        for (const dot of row.dots) {
          if (dot.at == null) continue;
          if (dot.at <= at) latest = dot;
          else break;
        }
        if (latest?.latency != null) values[row.name] = latest.latency;
      }
      series.push({ at, values });
    }
    result[site] = series;
  }
  return result;
}
