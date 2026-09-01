/** 与后端 llm_price_monitor 输出结构对应的类型定义。 */

export interface DiscountInfo {
  input: number | null;
  output: number | null;
  official_input_cny: number | null;
  official_output_cny: number | null;
  basis: string;
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
  source_url: string;
  captured_at: number;
  fingerprint: string;
  metadata?: {
    group?: string;
    confidence?: number;
    notes?: string;
    [key: string]: unknown;
  };
}

export interface OverviewRecord extends PriceRecord {
  discount: DiscountInfo | null;
}

export interface OfficialMeta {
  enabled: boolean;
  generated_at?: number;
  generated_at_iso?: string;
  usd_cny_rate?: number;
  rate_source?: string;
}

export interface OverviewData {
  records: OverviewRecord[];
  official: OfficialMeta;
}

export interface EventRow {
  site_id: string;
  model: string;
  kind: string;
  detected_at: number;
  current?: PriceRecord | null;
  previous?: PriceRecord | null;
}

export interface EventListData {
  events: EventRow[];
  total: number;
}

export interface HistoryListData {
  records: PriceRecord[];
  total: number;
}

export interface OfficialEntry {
  found: boolean;
  model: string;
  vendor: string;
  currency: string;
  list?: { input?: number; output?: number };
  promo?: { [key: string]: unknown } | null;
  effective?: { input?: number; output?: number; basis?: string };
  source_url?: string;
  from_previous_run?: boolean;
}

export interface OfficialData {
  generated_at_iso: string;
  usd_cny_rate: number;
  rate_source: string;
  models: Record<string, OfficialEntry>;
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
  auth_enabled: boolean;
  is_admin: boolean;
}

/** 站点完整配置（与 config/price-monitor.json 的 sites 段同构，存储于 SQLite sites 表）。 */
export interface SiteConfig {
  id: string;
  adapter?: string;
  model_list_url?: string | null;
  models: (string | { name: string; group?: string | null; aliases?: string[] })[];
  network?: {
    url?: string | null;
    method?: string;
    params?: Record<string, string>;
    headers?: Record<string, string>;
    body_type?: string;
    body?: unknown;
  } | null;
  request_headers?: Record<string, string> | null;
  enabled?: boolean;
  note?: string | null;
  [key: string]: unknown;
}

export interface SitesData {
  sites: SiteConfig[];
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
}
