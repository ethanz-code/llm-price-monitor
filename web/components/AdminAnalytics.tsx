"use client";

/** 访问统计面板：KPI + 近 30 天 PV/UV 柱状趋势 + 设备环形图 + 浏览器/系统/热门页面
 *  横向条形 + 最近访问明细表。数据来自 /api/analytics/*（浏览器侧经同源代理，
 *  携带管理员会话 cookie）。 */

import { useCallback, useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip as ReTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { apiSend } from "@/lib/api";
import { formatTime } from "@/lib/format";
import type { AnalyticsSummary, VisitLog, VisitLogsData } from "@/lib/types";
import { ChartBubble, useChartTheme } from "./chartTheme";
import { DataTable, type DColumn } from "./DataTable";
import { SiteAlert } from "./SiteAlert";
import { VisitorMap } from "./VisitorMap";
import { Btn, toast } from "./ui";

const DEVICE_LABELS: Record<string, string> = {
  desktop: "桌面端",
  mobile: "移动端",
  tablet: "平板",
  bot: "爬虫/脚本",
};

function deviceLabel(name: string): string {
  return DEVICE_LABELS[name] ?? name;
}

interface TrendTooltipEntry {
  payload?: { day?: string; pv?: number; uv?: number };
}

function TrendTooltip({ active, payload }: { active?: boolean; payload?: TrendTooltipEntry[] }) {
  if (!active || !payload?.length) return null;
  const point = payload[0]?.payload;
  if (!point) return null;
  return (
    <ChartBubble
      label={point.day}
      rows={[
        { name: "访问量", value: point.pv ?? 0 },
        { name: "访客数", value: point.uv ?? 0 },
      ]}
    />
  );
}

interface SimpleTooltipEntry {
  payload?: { name?: string; pv?: number };
}

function SimpleTooltip({ active, payload, label }: { active?: boolean; payload?: SimpleTooltipEntry[]; label?: string }) {
  if (!active || !payload?.length) return null;
  const point = payload[0]?.payload;
  return <ChartBubble label={point?.name ?? label} rows={[{ name: "访问量", value: point?.pv ?? 0 }]} />;
}

/** 横向条形图：名称在左、条上带数值，高度随条目数伸缩。 */
function NamedBars({ data, color, axisColor }: { data: { name: string; pv: number }[]; color: string; axisColor: string }) {
  if (!data.length) return <p className="empty">暂无数据</p>;
  return (
    <div style={{ height: data.length * 30 + 16 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 0, right: 44, bottom: 0, left: 0 }}>
          <XAxis type="number" hide allowDecimals={false} />
          <YAxis
            type="category"
            dataKey="name"
            width={86}
            tick={{ fill: axisColor, fontSize: 12 }}
            tickLine={false}
            axisLine={false}
          />
          <ReTooltip content={<SimpleTooltip />} cursor={{ fill: "rgba(127,127,127,0.12)" }} />
          <Bar dataKey="pv" fill={color} radius={[0, 3, 3, 0]} maxBarSize={14}>
            <LabelList
              dataKey="pv"
              position="right"
              style={{ fill: "var(--text-2)", fontSize: 11, fontFamily: "var(--mono)" }}
            />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** 设备分布环形图 + 右侧图例（含占比）。 */
function DeviceDonut({ data, palette }: { data: { name: string; pv: number }[]; palette: string[] }) {
  const total = data.reduce((sum, item) => sum + item.pv, 0);
  if (!data.length) return <p className="empty">暂无数据</p>;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 24, flexWrap: "wrap" }}>
      <div style={{ width: 190, height: 190, flexShrink: 0 }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data.map((item) => ({ ...item, name: deviceLabel(item.name) }))}
              dataKey="pv"
              nameKey="name"
              innerRadius="64%"
              outerRadius="92%"
              paddingAngle={2}
              strokeWidth={0}
            >
              {data.map((item, index) => (
                <Cell key={item.name} fill={palette[index % palette.length]} />
              ))}
            </Pie>
            <ReTooltip content={<SimpleTooltip />} />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 8 }}>
        {data.map((item, index) => (
          <li key={item.name} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
            <span aria-hidden style={{ width: 8, height: 8, borderRadius: 2, background: palette[index % palette.length], flexShrink: 0 }} />
            <span>{deviceLabel(item.name)}</span>
            <span className="mono" style={{ color: "var(--text-2)" }}>
              {item.pv} · {total ? Math.round((item.pv / total) * 100) : 0}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function VisitColumns(): DColumn<VisitLog>[] {
  return [
    { title: "时间", dataIndex: "ts", width: 150, sorter: (a, b) => a.ts - b.ts, defaultSortOrder: "descend", render: (value: number) => <span className="mono">{formatTime(value)}</span> },
    { title: "页面", dataIndex: "path", render: (value: string) => <span className="mono">{value}</span> },
    { title: "IP", dataIndex: "ip", width: 130, render: (value: string) => <span className="mono">{value || "—"}</span> },
    { title: "浏览器", dataIndex: "browser", width: 100, mobileHide: true },
    { title: "系统", dataIndex: "os", width: 90, mobileHide: true },
    { title: "设备", dataIndex: "device", width: 90, render: (value: string) => deviceLabel(value) },
  ];
}

export function AdminAnalytics() {
  const { lineColor, secondaryColor, axisColor, gridColor, palette } = useChartTheme();
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [visits, setVisits] = useState<VisitLog[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);

  const load = useCallback(async () => {
    try {
      const [summaryData, logsData] = await Promise.all([
        apiSend<AnalyticsSummary>("/api/analytics/summary", "GET"),
        apiSend<VisitLogsData>("/api/analytics/logs?limit=500", "GET"),
      ]);
      setSummary(summaryData);
      setVisits(logsData.visits);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function clearVisits() {
    if (!confirmClear) {
      setConfirmClear(true);
      return;
    }
    setConfirmClear(false);
    try {
      await apiSend("/api/analytics/clear", "POST");
      toast("访问记录已清空");
      await load();
    } catch (cause) {
      toast(cause instanceof Error ? cause.message : "清空失败");
    }
  }

  if (error) {
    return <SiteAlert title="暂时读不到访问统计" detail={error} fix="稍后再试，或检查服务是否已启动。" />;
  }
  if (!summary) {
    return <p className="empty" style={{ padding: "40px 0" }}>加载中…</p>;
  }

  const mapRegions = summary.regions.filter((item) => !["未知"].includes(item.name));
  const otherRegions = summary.regions.filter((item) => ["未知"].includes(item.name));

  return (
    <div style={{ display: "grid", gap: 32 }}>
      {mapRegions.length > 0 && (
        <section>
          <h3 className="section-title">访客来自哪里</h3>
          <p className="section-sub">近 30 天访问按国家着色；亮色主题勾线、暗色主题填色，越亮访问越多</p>
          <div style={{ border: "1px solid var(--border, rgba(127,127,127,0.2))", borderRadius: 12, overflow: "hidden" }}>
            <div style={{ height: "min(52vh, 480px)", minHeight: 320 }}>
              <VisitorMap regions={mapRegions} />
            </div>
          </div>
          {otherRegions.length > 0 && (
            <p style={{ color: "var(--text-3)", fontSize: 12, margin: "6px 0 0" }}>
              {otherRegions.map((item) => `${item.name} ${item.pv}`).join(" · ")}
            </p>
          )}
        </section>
      )}

      {summary.total_pv === 0 ? (
        <p className="empty" style={{ padding: "40px 0" }}>
          还没有访问记录；有人打开页面后就会开始累计，稍后再回来看看。
        </p>
      ) : (
        <>
          <section>
            <h3 className="section-title">近 30 天访问趋势</h3>
            <div style={{ height: 240, marginTop: 10 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={summary.daily} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
                  <CartesianGrid stroke={gridColor} vertical={false} />
                  <XAxis
                    dataKey="day"
                    tick={{ fill: axisColor, fontSize: 11 }}
                    tickLine={false}
                    axisLine={{ stroke: gridColor }}
                    tickMargin={8}
                    minTickGap={24}
                    interval="preserveStartEnd"
                  />
                  <YAxis allowDecimals={false} width={44} tick={{ fill: axisColor, fontSize: 11 }} tickLine={false} axisLine={false} />
                  <ReTooltip content={<TrendTooltip />} cursor={{ fill: "rgba(127,127,127,0.12)" }} />
                  <Bar dataKey="pv" name="访问量" fill={lineColor} radius={[3, 3, 0, 0]} maxBarSize={16} />
                  <Bar dataKey="uv" name="访客数" fill={secondaryColor} radius={[3, 3, 0, 0]} maxBarSize={16} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <p style={{ color: "var(--text-3)", fontSize: 12, margin: "6px 0 0" }}>
              绿色柱为访问量（PV），灰色柱为访客数（独立 IP）
            </p>
          </section>

          <section>
            <h3 className="section-title">设备与终端分布</h3>
            <p className="section-sub">近 30 天访问</p>
            <div style={{ display: "grid", gap: 28, gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))" }}>
              <div>
                <div style={{ fontSize: 13, color: "var(--text-2)", marginBottom: 8 }}>设备类型</div>
                <DeviceDonut data={summary.devices} palette={palette} />
              </div>
              <div>
                <div style={{ fontSize: 13, color: "var(--text-2)", marginBottom: 8 }}>浏览器</div>
                <NamedBars data={summary.browsers.slice(0, 7)} color={lineColor} axisColor={axisColor} />
              </div>
              <div>
                <div style={{ fontSize: 13, color: "var(--text-2)", marginBottom: 8 }}>操作系统</div>
                <NamedBars data={summary.oses.slice(0, 7)} color={secondaryColor} axisColor={axisColor} />
              </div>
            </div>
          </section>

          <section>
            <h3 className="section-title">热门页面</h3>
            <p className="section-sub">近 30 天访问量最高的页面</p>
            <NamedBars data={summary.top_paths.map((item) => ({ name: item.path, pv: item.pv }))} color={lineColor} axisColor={axisColor} />
          </section>
        </>
      )}

      <section>
        <h3 className="section-title">最近访问明细</h3>
        <p className="section-sub">
          同一 IP 30 秒内重复打开同一页面只计一次；记录自动保留 {summary.retained_days} 天
        </p>
        <DataTable columns={VisitColumns()} rows={visits} rowKey={(row) => `${row.ts}:${row.path}:${row.ip}`} paginated empty="暂无访问记录" />
      </section>

      <div  style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <Btn variant="ghost" size="sm" onClick={clearVisits}>
          {confirmClear ? "再点一次确认清空" : "清空访问记录"}
        </Btn>
        {confirmClear && (
          <button type="button" className="mono" style={{ background: "none", border: "none", color: "var(--text-3)", cursor: "pointer", fontSize: 12, padding: 0 }} onClick={() => setConfirmClear(false)}>
            取消
          </button>
        )}
      </div>
    </div>
  );
}
