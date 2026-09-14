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


/** 分桶保峰抽稀：把时间升序序列按时间跨度等分成 max 桶，每桶保留一个代表点——
 *  worse 返回桶内更"坏"的那个（可用率取最低、延迟取最高），平稳桶自然只剩普通点；
 *  始终保留序列最后一个点。短暂故障不会被均匀抽稀跳过，点数上限稳定可控。 */
export function downsampleWorst<T extends { at: number }>(points: T[], max: number, worse: (a: T, b: T) => T): T[] {
  if (points.length <= max) return points;
  const span = points[points.length - 1].at - points[0].at;
  const size = Math.max(span / max, 1);
  const out: T[] = [];
  let index = 0;
  while (index < points.length) {
    const limit = points[index].at + size;
    let rep = points[index];
    while (index < points.length && points[index].at < limit) {
      if (worse(points[index], rep) === points[index]) rep = points[index];
      index += 1;
    }
    out.push(rep);
  }
  if (out[out.length - 1] !== points[points.length - 1]) out.push(points[points.length - 1]);
  return out;
}

/** 趋势序列抽稀后的点数上限：7 天分钟级约 1 万个快照，抽到这个量渲染依旧轻快。 */
const MAX_SERIES_POINTS = 1440;

/** 各渠道检测点序列（时间升序、只留带时间戳的点）与一个只进不退的游标。 */
interface DotCursor {
  name: string;
  dots: (ChannelDot & { at: number })[];
  index: number;
}

/** 游标推进到时刻 at，返回该渠道在 at 时最新的检测点；at 早于其全部点时返回 null。 */
function latestAt(cursor: DotCursor, at: number): ChannelDot | null {
  while (cursor.index + 1 < cursor.dots.length && cursor.dots[cursor.index + 1].at <= at) cursor.index += 1;
  const dot = cursor.dots[cursor.index];
  return dot && dot.at <= at ? dot : null;
}

/** 成功率窗口：取最近 15 次检测，与总览渠道点阵单行展示的列数一致。 */
const RECENT_DOT_WINDOW = 15;

/** 一组检测点的成功率：最近检测的正常占比（0–1）；无数据返回 null。 */
export function dotsSuccessRate(dots: ChannelDot[] | undefined): number | null {
  if (!dots || dots.length === 0) return null;
  const recent = dots.slice(-RECENT_DOT_WINDOW);
  return recent.filter((dot) => dot.ok).length / recent.length;
}

/** 严格取某分组的检测点：只认渠道行名对得上的行（不做「匹配不上就全量」的回退），
 *  同名多行（写法差异）的点合并后按时间升序返回；没有匹配行返回 null。
 *  总览渠道列与排序扣分共用，保证「这一行的渠道列 = 这一行分组的可用度」。 */
export function dotsOfGroup(dots: ChannelDotRow[] | undefined, group: string | undefined): ChannelDot[] | null {
  if (!dots || dots.length === 0 || !group) return null;
  const target = normalizeChannelName(group);
  const merged = dots
    .filter((row) => normalizeChannelName(row.name) === target)
    .flatMap((row) => row.dots)
    .sort((a, b) => (a.at ?? 0) - (b.at ?? 0));
  return merged.length > 0 ? merged : null;
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
const MAX_DOTS = 720;

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

/** 可用率三档：<60% 大面积异常、60–80% 有渠道异常、≥80% 正常。 */
export type RateLevel = "ok" | "warn" | "down";

/** 按当次正常渠道占比归档；阈值与趋势图分段着色、KPI 数字着色共用。 */
export function rateLevel(pct: number): RateLevel {
  // 多分组聚合口径：平均可用率 80% 以上算优秀，60% 是及格线
  if (pct >= 80) return "ok";
  if (pct >= 60) return "warn";
  return "down";
}

/** 时段可用率色块的一个桶：起止时间、检测次数、平均/最差正常率与异常渠道名单。 */
export interface UptimeBucket {
  start: number;
  end: number;
  count: number;
  avg: number;
  worst: number;
  down: string[];
}

/** 时段色块分桶：把检测点按时间跨度等分成 ~target 段，每段聚合平均/最差正常率与异常名单。
 *  在数据源头（服务端）预计算，客户端只渲染桶，不再传输整段检测点。 */
export function buildUptimeBuckets(points: AvailabilityPoint[], target = 28): (UptimeBucket | null)[] {
  if (points.length === 0) return [];
  const t0 = points[0].at;
  const t1 = points[points.length - 1].at;
  const size = Math.max((t1 - t0) / target, 1);
  const total = Math.min(Math.max(Math.ceil((t1 - t0) / size), 1), target);
  const slots: (UptimeBucket | null)[] = Array.from({ length: total }, () => null);
  for (const point of points) {
    const index = Math.min(Math.floor((point.at - t0) / size), total - 1);
    const bucket = slots[index];
    if (bucket) {
      bucket.end = point.at;
      bucket.count += 1;
      bucket.avg += point.pct;
      bucket.worst = Math.min(bucket.worst, point.pct);
      for (const name of point.down) {
        if (!bucket.down.includes(name)) bucket.down.push(name);
      }
    } else {
      slots[index] = { start: point.at, end: point.at, count: 1, avg: point.pct, worst: point.pct, down: [...point.down] };
    }
  }
  for (const bucket of slots) if (bucket) bucket.avg = Math.round(bucket.avg / bucket.count);
  return slots;
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
 * 站点自带逐分钟时间线时密度就是分钟级。全量快照用双指针合并（点序列已升序），
 * 再分桶保峰抽到 ~1440 点：异常桶保留最低点，短暂故障不会被抽丢。
 */
export function availabilityBySite(records: { site_id: string; captured_at: number; data: unknown }[]): Record<string, AvailabilityPoint[]> {
  return availabilityFromDots(channelDotsBySite(records));
}

function availabilityFromDots(bySite: Record<string, ChannelDotRow[]>): Record<string, AvailabilityPoint[]> {
  const result: Record<string, AvailabilityPoint[]> = {};
  for (const [site, rows] of Object.entries(bySite)) {
    const cursors: DotCursor[] = rows.map((row) => ({
      name: row.name,
      dots: row.dots.filter((dot): dot is ChannelDot & { at: number } => dot.at != null),
      index: 0,
    }));
    const times = new Set<number>();
    for (const cursor of cursors) for (const dot of cursor.dots) times.add(dot.at);
    const sorted = [...times].sort((a, b) => a - b);
    const full: AvailabilityPoint[] = [];
    for (const at of sorted) {
      let ok = 0;
      let total = 0;
      const down: string[] = [];
      for (const cursor of cursors) {
        const latest = latestAt(cursor, at);
        if (!latest) continue;
        total += 1;
        if (latest.ok) ok += 1;
        else down.push(cursor.name);
      }
      if (total > 0) full.push({ at, pct: Math.round((ok / total) * 100), down });
    }
    result[site] = downsampleWorst(full, MAX_SERIES_POINTS, (a, b) => (a.pct < b.pct ? a : b));
  }
  return result;
}

/** 延迟趋势上的一个点：时刻 at（秒）时各渠道的自报延迟（ms，缺席的渠道不在 values 里）。 */
export interface LatencyPoint {
  at: number;
  values: Record<string, number>;
}

/** 按站点把带延迟的检测点合并成「时刻 → 各渠道延迟」序列，供延迟趋势图使用。
 *  同样双指针 + 分桶保峰：异常桶保留延迟最高的点，毛刺不会被抽平。 */
export function latencyBySite(records: { site_id: string; captured_at: number; data: unknown }[]): Record<string, LatencyPoint[]> {
  return latencyFromDots(channelDotsBySite(records));
}

function latencyFromDots(bySite: Record<string, ChannelDotRow[]>): Record<string, LatencyPoint[]> {
  const result: Record<string, LatencyPoint[]> = {};
  for (const [site, rows] of Object.entries(bySite)) {
    const cursors: DotCursor[] = rows
      .filter((row) => row.dots.some((dot) => dot.latency != null))
      .map((row) => ({
        name: row.name,
        dots: row.dots.filter((dot): dot is ChannelDot & { at: number } => dot.at != null),
        index: 0,
      }));
    if (cursors.length === 0) continue;
    const times = new Set<number>();
    for (const cursor of cursors) for (const dot of cursor.dots) times.add(dot.at);
    const sorted = [...times].sort((a, b) => a - b);
    const full: LatencyPoint[] = [];
    for (const at of sorted) {
      const values: Record<string, number> = {};
      for (const cursor of cursors) {
        const latest = latestAt(cursor, at);
        if (latest?.latency != null) values[cursor.name] = latest.latency;
      }
      full.push({ at, values });
    }
    const peak = (point: LatencyPoint) => Math.max(...Object.values(point.values), 0);
    result[site] = downsampleWorst(full, MAX_SERIES_POINTS, (a, b) => (peak(a) >= peak(b) ? a : b));
  }
  return result;
}

/** 延迟模型的一个样本：某渠道某次检测的取值（无效值已被 pick 过滤）。 */
export interface ChannelSample {
  at: number;
  value: number;
}

/** 窄屏抽帧上限：小屏像素少，全量点既看不清也拖不动；抽到这个量依然顺滑。 */
export const NARROW_CHART_POINTS = 240;

/** 把各渠道的检测点对齐到统一时间轴：某时刻的值取「该渠道最近一次检测」的结果（阶梯保持），
 *  首次检测之前为 null。桌面端保留全部检测点；窄屏按桶保留「最坏」点，故障形状不丢。
 *  详情页延迟趋势图与站点分享图共用，保证两边画的是同一条线。 */
export function buildChannelModel(
  channels: ChannelDotRow[],
  pick: (dot: { ok: boolean; latency?: number }) => number | null,
  narrow: boolean,
  worse: (a: ChannelSample, b: ChannelSample) => ChannelSample,
) {
  const per = channels
    .map((channel) => ({
      name: channel.name,
      dots: channel.dots
        .filter((dot) => dot.at != null)
        .map((dot) => ({ at: dot.at as number, value: pick(dot) }))
        .filter((dot): dot is ChannelSample => dot.value != null),
    }))
    .filter((channel) => channel.dots.length > 0);
  const kept = narrow
    ? per.map((c) => ({ ...c, dots: downsampleWorst(c.dots, NARROW_CHART_POINTS, worse) }))
    : per;

  const seen = new Set<number>();
  kept.forEach((c) => c.dots.forEach((d) => seen.add(d.at)));
  const times = [...seen].sort((a, b) => a - b);

  const series = kept.map((c) => {
    const values: (number | null)[] = [];
    let cursor = 0;
    let last: number | null = null;
    for (const t of times) {
      while (cursor < c.dots.length && c.dots[cursor].at <= t) {
        last = c.dots[cursor].value;
        cursor += 1;
      }
      values.push(last);
    }
    return { name: c.name, values };
  });
  return { times, series };
}

/** 详情页视图集合：渠道行、可用率序列、延迟序列共享同一次 channelDotsBySite 全量走查，
 *  避免页面把同一批记录解析三遍。 */
export function buildSiteViews(records: { site_id: string; captured_at: number; data: unknown }[]): {
  channels: Record<string, ChannelDotRow[]>;
  availability: Record<string, AvailabilityPoint[]>;
  latency: Record<string, LatencyPoint[]>;
} {
  const bySite = channelDotsBySite(records);
  return {
    channels: bySite,
    availability: availabilityFromDots(bySite),
    latency: latencyFromDots(bySite),
  };
}
