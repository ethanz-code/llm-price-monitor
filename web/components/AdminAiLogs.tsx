"use client";

/** AI 请求日志：上方为调用统计（一行轻量数字 + 按天趋势/分布图表 + 用量推演 Popover，口径为全部保留记录），
 *  下方为明细表（场景/模型/耗时/token 用量/错误），一次拉全量由表格本地分页，详情按需取摘要全文。 */

import { useEffect, useState, type ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  ResponsiveContainer,
  Tooltip as ReTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { apiSend } from "@/lib/api";
import { formatCount, formatTime, successRateTone, toneText } from "@/lib/format";
import type { AiLogDailyPoint as DailyPoint, AiLogSummary } from "@/lib/types";
import { ChartBubble, useChartTheme } from "./chartTheme";
import { DataTable, type DColumn } from "./DataTable";
import { Btn, Empty, Modal, Sel, Switch, Tip, toast, LoadingRows } from "./ui";
import { IconChevronRight } from "./icons";
import { DajuSit } from "./DajuArt";

type AiLog = {
  id: number;
  ts: number;
  scene: string;
  model: string;
  status: string;
  duration_ms: number;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  error: string | null;
  /** 摘要原文只在单条详情接口返回；列表行没有这两个字段，点开详情时按需取 */
  prompt_excerpt?: string | null;
  response_excerpt?: string | null;
};

const SCENES = ["", "助手分类", "助手问答", "价格抽取", "公告提取", "token 分析"];
// 明细单次拉取上限（与后端 _AI_LOG_LIST_LIMIT 对齐）：超过时表格上方提示只显示最近一部分
const LOGS_LIST_LIMIT = 5000;
const STATUSES = [
  { value: "", label: "全部结果" },
  { value: "ok", label: "成功" },
  { value: "fallback", label: "换模型重试" },
  { value: "param_retry", label: "参数适配" },
  { value: "transport", label: "连接抖动" },
  { value: "error", label: "整次失败" },
];

function StatusTag({ status }: { status: string }) {
  if (status === "ok") return <span className="tag tag-quiet">成功</span>;
  if (status === "fallback") return <span className="tag tone-blue">换模型重试</span>;
  if (status === "param_retry") return <span className="tag tone-blue">参数适配</span>;
  if (status === "transport") return <span className="tag tone-yellow">连接抖动</span>;
  return <span className="tag tone-red">整次失败</span>;
}

/** 耗时：不足 1 秒按毫秒显示（662ms），超过按秒显示（5.4s）。 */
function durationText(ms: number | null): string {
  if (ms == null || !Number.isFinite(ms)) return "—";
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

/** token 用量：输入 / 输出分别标注；两者都缺但有合计时退回合计。 */
function tokensText(row: AiLog): string {
  if (row.prompt_tokens == null && row.completion_tokens == null) {
    return row.total_tokens == null ? "—" : `共 ${formatCount(row.total_tokens)}`;
  }
  return `入 ${formatCount(row.prompt_tokens)} · 出 ${formatCount(row.completion_tokens)}`;
}

/** 输入输出比：入 ≥ 出显示 3.2 : 1，反之 1 : 2.5；任一侧为 0 时显示 —。 */
function ioRatioText(summary: AiLogSummary): string {
  if (!summary.prompt_tokens || !summary.completion_tokens) return "—";
  const ratio = summary.prompt_tokens / summary.completion_tokens;
  return ratio >= 1 ? `${ratio.toFixed(1)} : 1` : `1 : ${(1 / ratio).toFixed(1)}`;
}

/** token 用量推演内容：按近 7 天日均外推未来 30 天消耗，挂在「token 总消耗」卡片的悬停气泡里（深色 tooltip，不用亮色文字变量）。 */
function TokenForecastContent({ daily }: { daily: DailyPoint[] }) {
  const days = Math.max(daily.length, 1);
  const prompt = daily.reduce((sum, d) => sum + d.prompt_tokens, 0);
  const completion = daily.reduce((sum, d) => sum + d.completion_tokens, 0);
  if (prompt + completion === 0) {
    return <span>近 7 天还没有 token 用量记录，先用几天再来推算。</span>;
  }
  const avgPrompt = Math.round(prompt / days);
  const avgCompletion = Math.round(completion / days);
  return (
    <span>
      近 7 天日均输入 {formatCount(avgPrompt)} · 输出 {formatCount(avgCompletion)}，合计{" "}
      {formatCount(avgPrompt + avgCompletion)} token。照这个节奏，未来 30 天大约用{" "}
      <strong>{formatCount((avgPrompt + avgCompletion) * 30)}</strong> token（输入{" "}
      {formatCount(avgPrompt * 30)} · 输出 {formatCount(avgCompletion * 30)}）。
      <span style={{ opacity: 0.7 }}>用量随站点数量和检查频率变化，仅按近期平均估算，仅供参考。</span>
    </span>
  );
}

function CallsTooltip({ active, payload }: { active?: boolean; payload?: { payload?: DailyPoint }[] }) {
  const { lineColor, palette } = useChartTheme();
  if (!active || !payload?.length) return null;
  const point = payload[0]?.payload;
  if (!point) return null;
  return (
    <ChartBubble
      label={point.day}
      rows={[
        { color: lineColor, name: "成功", value: point.ok },
        { color: palette[1], name: "换模型重试", value: point.fallback },
        { color: palette[2], name: "参数适配", value: point.param_retry },
        { color: palette[4], name: "连接抖动", value: point.transport },
        { color: palette[3], name: "整次失败", value: point.error },
      ]}
    />
  );
}

function TokensTooltip({ active, payload }: { active?: boolean; payload?: { payload?: DailyPoint }[] }) {
  const { lineColor, secondaryColor } = useChartTheme();
  if (!active || !payload?.length) return null;
  const point = payload[0]?.payload;
  if (!point) return null;
  return (
    <ChartBubble
      label={point.day}
      rows={[
        { color: lineColor, name: "输入 token", value: formatCount(point.prompt_tokens) },
        { color: secondaryColor, name: "输出 token", value: formatCount(point.completion_tokens) },
      ]}
    />
  );
}

function CallsBarTooltip({ active, payload }: { active?: boolean; payload?: { payload?: { name?: string; calls?: number } }[] }) {
  if (!active || !payload?.length) return null;
  const point = payload[0]?.payload;
  return <ChartBubble label={point?.name} rows={[{ name: "调用次数", value: point?.calls ?? 0 }]} />;
}

/** 按天调用趋势：成功/换模型重试/换参数重试/连接抖动/整次失败堆叠柱状，配色与明细表状态标签一致。 */
function CallsTrendChart({ data }: { data: DailyPoint[] }) {
  const { lineColor, palette, axisColor, gridColor } = useChartTheme();
  return (
    <div style={{ height: 240 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
          <CartesianGrid stroke={gridColor} vertical={false} />
          <XAxis
            dataKey="day"
            tick={{ fill: axisColor, fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: gridColor }}
            tickMargin={8}
            interval="preserveStartEnd"
          />
          <YAxis allowDecimals={false} width={44} tick={{ fill: axisColor, fontSize: 11 }} tickLine={false} axisLine={false} />
          <ReTooltip content={<CallsTooltip />} cursor={{ fill: "rgba(127,127,127,0.12)" }} />
          <Bar dataKey="ok" name="成功" stackId="calls" fill={lineColor} maxBarSize={18} />
          <Bar dataKey="fallback" name="换模型重试" stackId="calls" fill={palette[1]} maxBarSize={18} />
          <Bar dataKey="param_retry" name="参数适配" stackId="calls" fill={palette[2]} maxBarSize={18} />
          <Bar dataKey="transport" name="连接抖动" stackId="calls" fill={palette[4]} maxBarSize={18} />
          <Bar dataKey="error" name="整次失败" stackId="calls" fill={palette[3]} radius={[3, 3, 0, 0]} maxBarSize={18} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** token 按天消耗：输入/输出分组柱状。 */
function TokensTrendChart({ data }: { data: DailyPoint[] }) {
  const { lineColor, secondaryColor, axisColor, gridColor } = useChartTheme();
  return (
    <div style={{ height: 240 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
          <CartesianGrid stroke={gridColor} vertical={false} />
          <XAxis
            dataKey="day"
            tick={{ fill: axisColor, fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: gridColor }}
            tickMargin={8}
            interval="preserveStartEnd"
          />
          <YAxis
            allowDecimals={false}
            width={48}
            tick={{ fill: axisColor, fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(value: number) =>
              value >= 1000 ? `${Number((value / 1000).toFixed(1))}k` : String(value)
            }
          />
          <ReTooltip content={<TokensTooltip />} cursor={{ fill: "rgba(127,127,127,0.12)" }} />
          <Bar dataKey="prompt_tokens" name="输入 token" fill={lineColor} radius={[3, 3, 0, 0]} maxBarSize={16} />
          <Bar dataKey="completion_tokens" name="输出 token" fill={secondaryColor} radius={[3, 3, 0, 0]} maxBarSize={16} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** 横向条形图：名称在左、条上带数值，高度随条目数伸缩。 */
function NamedCallsBar({
  data,
  color,
  axisWidth = 86,
}: {
  data: { name: string; calls: number }[];
  color: string;
  axisWidth?: number;
}) {
  const { axisColor } = useChartTheme();
  if (!data.length) return <p className="empty">暂无数据</p>;
  return (
    <div style={{ height: data.length * 30 + 16 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 0, right: 44, bottom: 0, left: 0 }}>
          <XAxis type="number" hide allowDecimals={false} />
          <YAxis
            type="category"
            dataKey="name"
            width={axisWidth}
            tick={{ fill: axisColor, fontSize: 12 }}
            tickLine={false}
            axisLine={false}
          />
          <ReTooltip content={<CallsBarTooltip />} cursor={{ fill: "rgba(127,127,127,0.12)" }} />
          <Bar dataKey="calls" fill={color} radius={[0, 3, 3, 0]} maxBarSize={14}>
            <LabelList
              dataKey="calls"
              position="right"
              style={{ fill: "var(--text-2)", fontSize: 11, fontFamily: "var(--mono)" }}
            />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** 单条日志详情：prompt 与回复原文摘要；列表接口不带回这两列，点开时按 id 取全文。 */
function LogDetailModal({ log, onClose }: { log: AiLog; onClose: () => void }) {
  const [full, setFull] = useState<AiLog | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setFull(null);
    setFailed(false);
    apiSend<{ log: AiLog }>(`/api/ai-logs/${log.id}`, "GET")
      .then((data) => {
        if (alive) setFull(data.log);
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, [log.id]);

  const promptExcerpt = full?.prompt_excerpt ?? log.prompt_excerpt ?? null;
  const responseExcerpt = full?.response_excerpt ?? log.response_excerpt ?? null;
  return (
    <Modal open onClose={onClose} title={`${log.scene} · ${log.model}`} width={680}>
      <div style={{ display: "grid", gap: 10, fontSize: 13 }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10, color: "var(--text-2)" }}>
          <StatusTag status={log.status} />
          <span>{formatTime(log.ts)}</span>
          <span>耗时 {durationText(log.duration_ms)}</span>
          {(log.prompt_tokens != null || log.completion_tokens != null || log.total_tokens != null) && (
            <span>
              token 输入 {formatCount(log.prompt_tokens)} · 输出 {formatCount(log.completion_tokens)}
              {log.total_tokens != null ? ` · 合计 ${formatCount(log.total_tokens)}` : ""}
            </span>
          )}
        </div>
        {log.error && (
          <div style={{ color: "var(--tone-red-text)", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{log.error}</div>
        )}
        {failed && <div style={{ color: "var(--text-3)" }}>详情内容没加载出来，关掉重开试试。</div>}
        {!promptExcerpt && !responseExcerpt && !failed && full == null && <LoadingRows rows={3} />}
        {promptExcerpt && (
          <div>
            <div style={{ color: "var(--text-3)", marginBottom: 4 }}>发送内容</div>
            <div style={{ background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 10, padding: "10px 12px", maxHeight: 220, overflowY: "auto", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
              {promptExcerpt}
            </div>
          </div>
        )}
        {responseExcerpt && (
          <div>
            <div style={{ color: "var(--text-3)", marginBottom: 4 }}>模型回复</div>
            <div style={{ background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 10, padding: "10px 12px", maxHeight: 220, overflowY: "auto", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
              {responseExcerpt}
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}

export function AdminAiLogs() {
  const { lineColor, secondaryColor } = useChartTheme();
  const [summary, setSummary] = useState<AiLogSummary | null>(null);
  const [logs, setLogs] = useState<AiLog[]>([]);
  // 明细一次拉全量，交给 DataTable 本地分页；total 大于已加载数时提示只看到最近一部分
  const [total, setTotal] = useState(0);
  // 首次明细未回来前先画骨架，避免空表闪「还没有记录」的假空态
  const [logsReady, setLogsReady] = useState(false);
  const [scene, setScene] = useState("");
  const [status, setStatus] = useState("");
  const [detail, setDetail] = useState<AiLog | null>(null);

  // 统计口径固定为全部保留记录，不随下方筛选联动，只在进入页面时取一次
  useEffect(() => {
    let alive = true;
    apiSend<AiLogSummary>("/api/ai-logs/summary", "GET")
      .then((data) => {
        if (alive) setSummary(data);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;
    const query = new URLSearchParams({ limit: String(LOGS_LIST_LIMIT) });
    if (scene) query.set("scene", scene);
    if (status) query.set("status", status);
    apiSend<{ logs: AiLog[]; total: number }>(`/api/ai-logs?${query}`, "GET")
      .then((data) => {
        if (alive) {
          setLogs(data.logs);
          setTotal(data.total);
          setLogsReady(true);
        }
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [scene, status]);

  const hasCalls = (summary?.total ?? 0) > 0;
  // 有 token 用量记录才展示推演入口；日志没记到 usage（供应商没返回）时不给按钮
  const hasTokens = (summary?.total_tokens ?? 0) > 0;
  const successRate = summary ? summary.success_rate : null;
  // 失败 = 供应商报错且未被「报错判定」豁免的尝试；连接抖动与已忽略报错组不算失败也不进成功率分母
  const failure = summary ? summary.total - summary.ok - summary.transport - summary.ignored : 0;
  const [ignoredSaving, setIgnoredSaving] = useState(false);
  const [kindsOpen, setKindsOpen] = useState(false);
  const errorKinds = summary?.error_kinds ?? [];
  const ignoredCount = errorKinds.filter((kind) => kind.ignored).length;
  const ignoredKeys = new Set(errorKinds.filter((kind) => kind.ignored).map((kind) => kind.key));

  // 勾选报错组：存进 settings.ai_ignored_errors，成功后重取汇总刷新 KPI 与分组状态
  async function toggleErrorKind(key: string, ignore: boolean) {
    if (ignoredSaving) return;
    const next = new Set(ignoredKeys);
    if (ignore) next.add(key);
    else next.delete(key);
    setIgnoredSaving(true);
    try {
      await apiSend("/api/settings", "PUT", { settings: { ai_ignored_errors: [...next] } });
      setSummary(await apiSend<AiLogSummary>("/api/ai-logs/summary", "GET"));
    } catch {
      toast("没保存成功，请再试一次");
    } finally {
      setIgnoredSaving(false);
    }
  }

  const kpis: { label: ReactNode; value: string; hint: ReactNode; color?: string }[] = summary
    ? [
        {
          label: "调用总数",
          value: formatCount(summary.total),
          hint: (
            <>
              {failure > 0 ? (
                <span style={{ color: toneText("red") }}>失败 {formatCount(failure)}</span>
              ) : (
                `失败 ${formatCount(failure)}`
              )}
              {` · 换模型重试 ${formatCount(summary.fallback)} · 参数适配 ${formatCount(summary.param_retry)}`}
              {summary.transport > 0 ? ` · 连接抖动 ${formatCount(summary.transport)}` : ""}
              {summary.ignored > 0 ? ` · 已忽略 ${formatCount(summary.ignored)}` : ""}
            </>
          ),
        },
        {
          label: "成功率",
          value: successRate != null ? `${successRate.toFixed(1)}%` : "—",
          color: successRate != null ? toneText(successRateTone(successRate)) : undefined,
          hint: `成功 ${formatCount(summary.ok)} 次 · 失败 ${formatCount(failure)} 次`,
        },
        {
          label: (
            <>
              token 总消耗
              {hasTokens && (
                <Tip ariaLabel="用量推演" content={<TokenForecastContent daily={summary.daily} />} />
              )}
            </>
          ),
          value: formatCount(summary.total_tokens),
          hint: `输入 ${formatCount(summary.prompt_tokens)} · 输出 ${formatCount(summary.completion_tokens)}`,
        },
        { label: "输入输出比", value: ioRatioText(summary), hint: "输入 token ÷ 输出 token" },
        { label: "平均耗时", value: durationText(summary.avg_duration_ms), hint: "全部调用的均值" },
      ]
    : [];

  const columns: DColumn<AiLog>[] = [
    { key: "ts", title: "时间", width: 150, render: (_v, row) => <span className="mono" style={{ color: "var(--text-2)", fontSize: 13, whiteSpace: "nowrap" }}>{formatTime(row.ts)}</span> },
    { key: "scene", title: "场景", width: 100 },
    { key: "model", title: "模型", width: 180, render: (_v, row) => <span style={{ fontSize: 12.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: "block" }}>{row.model}</span> },
    { key: "status", title: "结果", width: 110, render: (_v, row) => <StatusTag status={row.status} /> },
    { key: "duration_ms", dataIndex: "duration_ms", title: "耗时", width: 90, align: "right", render: (_v, row) => <span className="mono" style={{ fontSize: 12.5 }}>{durationText(row.duration_ms)}</span> },
    { key: "tokens", title: "token 输入 / 输出", width: 175, align: "right", render: (_v, row) => <span className="mono" style={{ fontSize: 12.5 }}>{tokensText(row)}</span> },
    {
      key: "error",
      title: "备注",
      render: (_v, row) =>
        row.error ? (
          <span style={{ color: "var(--tone-red-text)", fontSize: 12.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: "block" }}>{row.error}</span>
        ) : null,
    },
    {
      key: "detail",
      title: "详情",
      width: 70,
      render: (_v, row) => (
        <Btn variant="ghost" size="sm" onClick={() => setDetail(row)}>
          查看
        </Btn>
      ),
    },
  ];

  return (
    <div style={{ display: "grid", gap: 12, gridTemplateColumns: "minmax(0, 1fr)" }}>
      {detail && <LogDetailModal log={detail} onClose={() => setDetail(null)} />}
      {hasCalls && summary && (
        <section style={{ display: "grid", gap: 14, marginBottom: 8 }}>
          <div className="dash-stats">
            {kpis.map((card, index) => (
              <div key={index} className="dash-stat">
                <div className="dash-stat-label">{card.label}</div>
                <div className="dash-stat-value" style={card.color ? { color: card.color } : undefined}>
                  {card.value}
                  <span className="dash-stat-hint">{card.hint}</span>
                </div>
              </div>
            ))}
          </div>
          <p style={{ color: "var(--text-3)", fontSize: 12, margin: 0 }}>
            统计口径：保留期内每次请求尝试各记一条，成功率 = 成功尝试 ÷ 有效尝试。报错后自动换模型、换参数的尝试计入失败，误判的报错组可在「报错判定」里关掉；连接抖动（超时、SSL 断开）不算失败也不进分母；模型池全部报错记一条「整次失败」。
          </p>
          <div className="panel" style={{ padding: "16px 20px 18px", display: "grid", gap: 28, gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))" }}>
            <div>
              <div style={{ fontSize: 13, color: "var(--text-2)", marginBottom: 8 }}>按天调用趋势</div>
              <CallsTrendChart data={summary.daily} />
            </div>
            <div>
              <div style={{ fontSize: 13, color: "var(--text-2)", marginBottom: 8 }}>token 按天消耗</div>
              <TokensTrendChart data={summary.daily} />
            </div>
            <div>
              <div style={{ fontSize: 13, color: "var(--text-2)", marginBottom: 8 }}>场景分布</div>
              <NamedCallsBar data={summary.scenes} color={lineColor} />
            </div>
            <div>
              <div style={{ fontSize: 13, color: "var(--text-2)", marginBottom: 8 }}>模型分布</div>
              <NamedCallsBar data={summary.models} color={secondaryColor} axisWidth={130} />
            </div>
          </div>
        </section>
      )}
      {hasCalls && summary && errorKinds.length > 0 && (
        <section className="panel" style={{ padding: "16px 20px 18px", display: "grid", gap: 12, gridTemplateColumns: "minmax(0, 1fr)" }}>
          <button type="button" className="disclosure-row" aria-expanded={kindsOpen} onClick={() => setKindsOpen(!kindsOpen)}>
            <span className="caret" aria-hidden>
              <IconChevronRight size={13} />
            </span>
            <span style={{ fontWeight: 500, whiteSpace: "nowrap" }}>报错判定</span>
            <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--text-3)" }}>
              {errorKinds.length} 组报错{ignoredCount > 0 ? `，${ignoredCount} 组已忽略` : ""}
            </span>
          </button>
          {kindsOpen && (
            <>
              <p style={{ fontSize: 12, color: "var(--text-3)", margin: 0 }}>
                历史报错按形态自动归为一组：打开开关的组不算失败，也不计入成功率。
              </p>
              {errorKinds.map((kind) => (
                <div key={kind.key} style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <Switch
                    checked={kind.ignored}
                    disabled={ignoredSaving}
                    title="打开后这一组报错不算失败、不进成功率"
                    onChange={(next) => toggleErrorKind(kind.key, next)}
                  />
                  <span className="mono" style={{ fontSize: 12.5, color: "var(--text-2)", whiteSpace: "nowrap" }}>
                    {kind.count} 次
                  </span>
                  <span
                    title={kind.sample}
                    style={{
                      flex: 1,
                      fontSize: 12.5,
                      color: kind.ignored ? "var(--text-3)" : "var(--text-2)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {kind.sample}
                  </span>
                  {kind.ignored ? (
                    <span className="tag tone-gray">已忽略</span>
                  ) : (
                    <span className="tag tone-red">算失败</span>
                  )}
                </div>
              ))}
            </>
          )}
        </section>
      )}
      <div style={{ display: "flex", gap: 8 }}>
        <Sel
          value={scene}
          onChange={setScene}
          ariaLabel="按场景筛选"
          options={SCENES.map((item) => ({ value: item, label: item === "" ? "全部场景" : item }))}
        />
        <Sel value={status} onChange={setStatus} ariaLabel="按结果筛选" options={STATUSES} />
      </div>
      <div className="panel" style={{ overflow: "hidden" }}>
        {!logsReady ? (
          <div style={{ padding: "16px 20px" }}>
            <LoadingRows rows={7} />
          </div>
        ) : (
        <DataTable<AiLog>
          rowKey="id"
          columns={columns}
          rows={logs}
          paginated
          scrollX={900}
          mobileScrollX={850}
          empty={
            <Empty
              icon={<DajuSit width={30} />}
              title="还没有 AI 调用记录"
              description="助手问答、价格抽取等用到 AI 的操作发生后会显示在这里。"
            />
          }
        />
        )}
      </div>
      {logsReady && logs.length < total && (
        <p style={{ color: "var(--text-3)", fontSize: 12, margin: 0 }}>
          记录太多，这里只显示最近 {formatCount(logs.length)} 条（共 {formatCount(total)} 条）；用上方筛选缩小范围可以看到更早的。
        </p>
      )}
    </div>
  );
}
