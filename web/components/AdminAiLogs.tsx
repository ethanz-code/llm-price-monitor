"use client";

/** AI 请求日志：上方为调用统计（一行轻量数字 + 按天趋势/分布图表，口径为全部保留记录），
 *  下方为明细表（场景/模型/耗时/token 用量/错误），明细支持按场景与结果筛选。 */

import { useEffect, useState } from "react";
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
import { formatTime } from "@/lib/format";
import { ChartBubble, useChartTheme } from "./chartTheme";
import { DataTable, type DColumn } from "./DataTable";
import { Btn, Empty, Modal, Sel } from "./ui";
import { IconAim } from "./icons";

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
  prompt_excerpt: string | null;
  response_excerpt: string | null;
};

type DailyPoint = {
  day: string;
  ok: number;
  fallback: number;
  param_retry: number;
  error: number;
  prompt_tokens: number;
  completion_tokens: number;
};

type AiLogSummary = {
  total: number;
  ok: number;
  fallback: number;
  param_retry: number;
  error: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  avg_duration_ms: number;
  daily: DailyPoint[];
  scenes: { name: string; calls: number }[];
  models: { name: string; calls: number }[];
};

const SCENES = ["", "助手分类", "助手问答", "价格抽取", "公告提取", "token 分析"];
const STATUSES = [
  { value: "", label: "全部结果" },
  { value: "ok", label: "成功" },
  { value: "fallback", label: "换模型重试" },
  { value: "param_retry", label: "换参数重试" },
  { value: "error", label: "失败" },
];

function StatusTag({ status }: { status: string }) {
  if (status === "ok") return <span className="tag tone-green">成功</span>;
  if (status === "fallback") return <span className="tag tone-blue">换模型重试</span>;
  if (status === "param_retry") return <span className="tag tone-blue">换参数重试</span>;
  return <span className="tag tone-red">失败</span>;
}

/** 千分位整数：3006 → 3,006；没有用量时显示 —。 */
function countText(value: number | null): string {
  return value == null ? "—" : value.toLocaleString("en-US");
}

/** 耗时：不足 1 秒按毫秒显示（662ms），超过按秒显示（5.4s）。 */
function durationText(ms: number | null): string {
  if (ms == null || !Number.isFinite(ms)) return "—";
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

/** token 用量：输入 / 输出分别标注；两者都缺但有合计时退回合计。 */
function tokensText(row: AiLog): string {
  if (row.prompt_tokens == null && row.completion_tokens == null) {
    return row.total_tokens == null ? "—" : `共 ${countText(row.total_tokens)}`;
  }
  return `入 ${countText(row.prompt_tokens)} · 出 ${countText(row.completion_tokens)}`;
}

/** 输入输出比：入 ≥ 出显示 3.2 : 1，反之 1 : 2.5；任一侧为 0 时显示 —。 */
function ioRatioText(summary: AiLogSummary): string {
  if (!summary.prompt_tokens || !summary.completion_tokens) return "—";
  const ratio = summary.prompt_tokens / summary.completion_tokens;
  return ratio >= 1 ? `${ratio.toFixed(1)} : 1` : `1 : ${(1 / ratio).toFixed(1)}`;
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
        { color: palette[2], name: "换参数重试", value: point.param_retry },
        { color: palette[3], name: "失败", value: point.error },
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
        { color: lineColor, name: "输入 token", value: point.prompt_tokens.toLocaleString("en-US") },
        { color: secondaryColor, name: "输出 token", value: point.completion_tokens.toLocaleString("en-US") },
      ]}
    />
  );
}

function CallsBarTooltip({ active, payload }: { active?: boolean; payload?: { payload?: { name?: string; calls?: number } }[] }) {
  if (!active || !payload?.length) return null;
  const point = payload[0]?.payload;
  return <ChartBubble label={point?.name} rows={[{ name: "调用次数", value: point?.calls ?? 0 }]} />;
}

/** 按天调用趋势：成功/换模型重试/换参数重试/失败堆叠柱状，配色与明细表状态标签一致。 */
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
          <Bar dataKey="param_retry" name="换参数重试" stackId="calls" fill={palette[2]} maxBarSize={18} />
          <Bar dataKey="error" name="失败" stackId="calls" fill={palette[3]} radius={[3, 3, 0, 0]} maxBarSize={18} />
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

/** 单条日志详情：prompt 与回复原文摘要。 */
function LogDetailModal({ log, onClose }: { log: AiLog; onClose: () => void }) {
  return (
    <Modal open onClose={onClose} title={`${log.scene} · ${log.model}`} width={680}>
      <div style={{ display: "grid", gap: 10, fontSize: 13 }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10, color: "var(--text-2)" }}>
          <StatusTag status={log.status} />
          <span>{formatTime(log.ts)}</span>
          <span>耗时 {durationText(log.duration_ms)}</span>
          {(log.prompt_tokens != null || log.completion_tokens != null || log.total_tokens != null) && (
            <span>
              token 输入 {countText(log.prompt_tokens)} · 输出 {countText(log.completion_tokens)}
              {log.total_tokens != null ? ` · 合计 ${countText(log.total_tokens)}` : ""}
            </span>
          )}
        </div>
        {log.error && (
          <div style={{ color: "var(--tone-red-text)", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{log.error}</div>
        )}
        {log.prompt_excerpt && (
          <div>
            <div style={{ color: "var(--text-3)", marginBottom: 4 }}>发送内容</div>
            <div style={{ background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 10, padding: "10px 12px", maxHeight: 220, overflowY: "auto", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
              {log.prompt_excerpt}
            </div>
          </div>
        )}
        {log.response_excerpt && (
          <div>
            <div style={{ color: "var(--text-3)", marginBottom: 4 }}>模型回复</div>
            <div style={{ background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 10, padding: "10px 12px", maxHeight: 220, overflowY: "auto", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
              {log.response_excerpt}
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
    const query = new URLSearchParams({ limit: "200" });
    if (scene) query.set("scene", scene);
    if (status) query.set("status", status);
    apiSend<{ logs: AiLog[] }>(`/api/ai-logs?${query}`, "GET")
      .then((data) => {
        if (alive) setLogs(data.logs);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [scene, status]);

  const hasCalls = (summary?.total ?? 0) > 0;
  const successRate = summary && summary.total ? (summary.ok / summary.total) * 100 : null;
  const kpis = summary
    ? [
        {
          label: "调用总数",
          value: summary.total.toLocaleString("en-US"),
          hint: `失败 ${summary.error} · 换模型重试 ${summary.fallback} · 换参数重试 ${summary.param_retry}`,
        },
        {
          label: "成功率",
          value: successRate != null ? `${successRate.toFixed(1)}%` : "—",
          hint: `成功 ${summary.ok} 次`,
        },
        {
          label: "token 总消耗",
          value: summary.total_tokens.toLocaleString("en-US"),
          hint: `输入 ${summary.prompt_tokens.toLocaleString("en-US")} · 输出 ${summary.completion_tokens.toLocaleString("en-US")}`,
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
    <div style={{ display: "grid", gap: 12 }}>
      {detail && <LogDetailModal log={detail} onClose={() => setDetail(null)} />}
      {hasCalls && summary && (
        <section style={{ display: "grid", gap: 14, marginBottom: 8 }}>
          <div className="dash-stats">
            {kpis.map((card) => (
              <div key={card.label} className="dash-stat">
                <div className="dash-stat-label">{card.label}</div>
                <div className="dash-stat-value">
                  {card.value}
                  <span className="dash-stat-hint">{card.hint}</span>
                </div>
              </div>
            ))}
          </div>
          <p style={{ color: "var(--text-3)", fontSize: 12, margin: 0 }}>
            统计口径：当前保留的全部调用（日志自动保留 7 天）；「换模型重试」「换参数重试」是同一请求里失败的尝试，不计入成功率。
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
        <DataTable<AiLog>
          rowKey="id"
          columns={columns}
          rows={logs}
          scrollX={900}
          mobileScrollX={850}
          empty={
            <Empty
              icon={<IconAim size={18} />}
              title="还没有 AI 调用记录"
              description="助手问答、价格抽取等用到 AI 的操作发生后会显示在这里。"
            />
          }
        />
      </div>
    </div>
  );
}
