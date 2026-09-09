/** 与后端 llm_price_monitor 输出结构对应的类型定义。 */

export interface DiscountInfo {
  input: number | null;
  output: number | null;
  input_price_cny: number | null;
  output_price_cny: number | null;
  official_input_cny: number | null;
  official_output_cny: number | null;
  source_url: string;
}

export interface PriceRecord {
  site_id: string;
  model: string;
  input_price: number | null;
  output_price: number | null;
  unit: string;
  price_status: string;
  requires_auth: boolean;
  status_reason?: string | null;
  /** 上次实际取到价的时间：本次采集没拿到数据、沿用上次价时由后端写入 */
  last_price_at?: number | null;
  source_url: string;
  captured_at: number;
  fingerprint: string;
  metadata?: {
    group?: string;
    confidence?: number;
    notes?: string;
    /** base_times_rate 记录：实售价 = 基准价(USD) × rate */
    rate?: number;
    rate_source?: string;
    [key: string]: unknown;
  };
}

export interface OverviewRecord extends PriceRecord {
  discount: DiscountInfo | null;
}

export interface CatalogMeta {
  enabled: boolean;
  generated_at?: number;
  generated_at_iso?: string;
  usd_cny_rate?: number;
  rate_source?: string;
}

export interface OverviewData {
  records: OverviewRecord[];
  catalog: CatalogMeta;
  collect_status?: Record<string, SiteStatus>;
  /** 各站点最新公告（每站一条；没采集到公告的站点无键） */
  notices?: Record<string, { content: string; captured_at?: number | null }>;
  /** 各站点配置信息完整度（0–3）：监控模型、认证凭证、附加采集地址各 1 分 */
  site_completeness?: Record<string, number>;
}

/** 单站点最近一次采集状态（后端 documents.collect_status，与 run_once/site_status_from_records 对齐）。 */
export interface SiteStatus {
  status: "ok" | "inferred" | "auth_required" | "no_data" | "unavailable" | "error" | "disabled";
  error: string | null;
  checked_at: number | null;
}

export interface EventRow {
  site_id: string;
  model: string;
  kind: string;
  detected_at: number;
  current?: PriceRecord | null;
  previous?: PriceRecord | null;
}

/** 趋势点（price_trend 精简列）：每次采集一条，供价格趋势图使用。 */
export interface TrendRecord {
  site_id: string;
  model: string;
  group?: string;
  input_price: number | null;
  output_price: number | null;
  unit: string;
  captured_at: number;
}

export interface HistoryListData {
  records: TrendRecord[];
  total: number;
  /** 展示汇率（USD→CNY，快照优先实时兜底）；缺失时前端回落原币展示 */
  rate?: number;
}

export interface CatalogEntry {
  found: boolean;
  model: string;
  /** models.dev 的展示名（如 Claude Sonnet 4.6） */
  name?: string;
  vendor: string;
  /** 厂商 logo 地址（models.dev 托管，仅全量渠道目录带；官方视图走本地内置 SVG） */
  logo?: string | null;
  currency: string;
  list?: { input?: number; output?: number };
  /** 快照汇率换算的人民币价（刷新时锁定），仅供展示 */
  list_cny?: { input?: number | null; output?: number | null };
  source_url?: string;
  description?: string;
  /** 简介的中文翻译（AI 随目录同步翻译；AI 未配置或未轮到时为空，回落英文原文） */
  description_zh?: string | null;
  /** models.dev 的产品线家族（如 gpt-astra / claude-opus），旗舰高亮的分组依据 */
  family?: string;
  /** 模态（models.dev 原始结构）；旗舰判定只认有文本输出的模型 */
  modalities?: { input?: string[]; output?: string[] };
  /** 厂商标称的 token 上限：上下文窗口与其中输入/输出的上限 */
  limit?: { context?: number; input?: number; output?: number };
  /** models.dev 的发布日期（YYYY-MM-DD，可能为 null）：全量表展示，也参与旗舰档位判定 */
  release_date?: string | null;
  /** AI 档位判定：flagship 顶级（整行高亮）/ mainstream 主流（名称旁 Tag）/ null 其他；AI 不可用时缺省 */
  tier?: "flagship" | "mainstream" | null;
}

export interface CatalogData {
  generated_at_iso: string;
  /** 数据来源标识与主页链接，恒为 models.dev */
  source: string;
  source_url?: string;
  usd_cny_rate: number;
  rate_source: string;
  models: Record<string, CatalogEntry>;
}

export interface DiscountRow extends DiscountInfo {
  site_id: string;
  model: string;
  group?: string | null;
}

export interface DiscountSummaryItem {
  model: string;
  input_discount: { min: number; max: number; avg: number };
  output_discount: { min: number; max: number; avg: number };
  sites_compared: number;
}

export interface DiscountData {
  usd_cny_rate: number;
  rate_source: string;
  official_generated_at?: string;
  discounts: DiscountRow[];
  summary: Record<string, DiscountSummaryItem>;
  skipped: { site_id?: string; model?: string; reason: string }[];
}

export interface SiteMeta {
  id: string;
  adapter: string;
  models: string[];
  url?: string;
  enabled: boolean;
}

export interface MetaData {
  sites: SiteMeta[];
  is_admin: boolean;
  /** true = 数据库还没有管理员账号，需要先走 /setup 首次设置 */
  needs_setup: boolean;
}

/** 附加采集地址（networks 数组项），与 network 同构。 */
export interface NetworkEndpoint {
  url?: string | null;
  params?: Record<string, string>;
  headers?: Record<string, string>;
}

/** 站点完整配置（与 config/default-seed.json 的 sites 段同构，存储于 SQLite sites 表）。 */
export interface SiteConfig {
  id: string;
  adapter?: string;
  models: string[];
  auth_token?: string | null;
  auth_header?: string | null;
  auth_prefix?: string | null;
  cookie?: string | null;
  cookies?: Record<string, string> | null;
  network?: {
    url?: string | null;
    /** 倍率接口：返回 {pricing: [{provider, model_display, rate}]}，基准价 × rate 折算实售价 */
    ratio_url?: string | null;
    params?: Record<string, string>;
    headers?: Record<string, string>;
    /** 网页模式·无头浏览器：开启后用无头浏览器打开网页并注入登录信息，可采集需要登录的页面 */
    headless?: {
      enabled?: boolean;
      /** 注入的 Cookie；每项只需 name/value，domain/path 由后端按站点 URL 域名自动补 */
      cookies?: { name: string; value: string }[];
      /** 注入的 localStorage 键值 */
      localStorage?: Record<string, string>;
      /** 页面加载后等待秒数（0~60），默认 3 */
      wait_seconds?: number;
    } | null;
  } | null;
  networks?: NetworkEndpoint[];
  /** 渠道状态数据地址：每次采集顺带 GET 并存档，变化写入状态事件 */
  status?: { url?: string | null; params?: Record<string, string>; headers?: Record<string, string> } | null;
  /** 站点公告地址：默认从 network.url 推导 /api/notice；也可指向纯文本/Markdown 公告页 */
  notice?: { url?: string | null; params?: Record<string, string>; headers?: Record<string, string> } | null;
  request_headers?: Record<string, string> | null;
  /** 认证续签：token 失效时调用该接口换新 token 并自动回写配置 */
  token_refresh?: {
    url: string;
    method?: string;
    params?: Record<string, string>;
    headers?: Record<string, string>;
    /** 请求体模板，${refresh_token} 会替换成下面的 refresh_token */
    body?: string;
    /** 响应数据结构案例：保存时交给 AI 分析出 token 字段路径，不落库 */
    response_sample?: string;
    /** 响应里 token 的字段路径（如 data.access_token）；留空自动探测 data.access_token / access_token */
    access_token_field?: string;
    refresh_token_field?: string;
    refresh_token: string;
  } | null;
  enabled?: boolean;
  [key: string]: unknown;
}

export interface SitesData {
  sites: SiteConfig[];
  collect_status?: Record<string, SiteStatus>;
}

/** 系统设置文档：settings/ai 两段，键名与配置文件一致。 */
export interface SettingsData {
  settings: Record<string, unknown>;
  ai: Record<string, unknown>;
}

export interface TasksData {
  tasks: TaskInfo[];
}

export interface TaskInfo {
  id: string;
  kind: string;
  status: "running" | "done" | "failed";
  started_at: number;
  finished_at: number | null;
  result?: { [key: string]: unknown };
  error?: string | null;
  /** 列表接口只带日志行数；完整日志经 /api/tasks/{id} 详情获取。 */
  log_count?: number;
  /** 日志中 level=error 的行数与最后一条错误内容，供列表行内提示。 */
  error_count?: number;
  error_summary?: string | null;
}

/** 任务过程日志行：level=error 在界面标红。 */
export interface TaskLog {
  time: number;
  message: string;
  level: "info" | "error";
}

export interface TaskDetail extends TaskInfo {
  logs: TaskLog[];
}

/** 渠道状态快照（后端 fetch_site_status 输出；data 为站点自有结构的自由 JSON）。 */
export interface StatusSnapshot {
  site_id: string;
  captured_at: number;
  source_url: string;
  http_status: number;
  parse: string;
  data: unknown;
}

/** /api/status/latest：site_id → 最近一次快照 */
export type StatusLatest = Record<string, StatusSnapshot>;

export interface StatusChange {
  op: "add" | "remove" | "change";
  path: string;
  old?: unknown;
  new?: unknown;
}

export interface StatusEvent {
  site_id: string;
  kind: string;
  detected_at: number;
  changes: StatusChange[];
}

/** 站点公告版本（后端 fetch_site_notice 输出；content 为公告正文 Markdown/纯文本，仅内容变化时新增）。 */
export interface NoticeSnapshot {
  site_id: string;
  captured_at: number;
  source_url: string;
  http_status: number;
  parse: string;
  content: string;
}

/** 站点公告事件：首次建档（notice_init）或内容更新（notice_changed）。 */
export interface NoticeEvent {
  site_id: string;
  kind: string;
  detected_at: number;
  content: string;
}

/** 事件流条目：价格事件（带模型与前后价格）或公告事件（带公告正文）。 */
export type FeedEvent = EventRow | NoticeEvent;

/** /api/feed：价格事件与公告事件按时间合并的统一事件流 */
export interface FeedData {
  events: FeedEvent[];
  /** 价格事件全量总数（events 可能被 limit 截断，计数用它才准确） */
  price_total: number;
  /** 公告事件全量总数 */
  notice_total: number;
}

/** 单条页面访问记录（后端 visit_logs 表，/api/analytics/logs 输出）。 */
export interface VisitLog {
  ts: number;
  path: string;
  ip: string;
  user_agent: string;
  browser: string;
  os: string;
  device: string;
}

export interface VisitLogsData {
  visits: VisitLog[];
  total: number;
}

/** 访问统计聚合（/api/analytics/summary 输出；分布与榜单取近 30 天窗口）。 */
export interface AnalyticsSummary {
  today_pv: number;
  today_uv: number;
  total_pv: number;
  total_ip: number;
  daily: { day: string; pv: number; uv: number }[];
  devices: { name: string; pv: number }[];
  browsers: { name: string; pv: number }[];
  oses: { name: string; pv: number }[];
  top_paths: { path: string; pv: number; uv: number }[];
  top_ips: { ip: string; pv: number; last_seen: number }[];
  /** 近 30 天按国家聚合的访问分布（中文名，匹配世界地图；"未知"为解析失败）。 */
  regions: { name: string; pv: number; uv: number }[];
  retained_days: number;
}
