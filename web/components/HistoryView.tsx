"use client";

import { useMemo, useState } from "react";
import { DataTable, type DColumn } from "./DataTable";
import { Empty, Sel, Seg } from "./ui";
import { ToneTag } from "./ToneTag";
import { RiskLink } from "./RiskLink";
import { getSiteInfo } from "@/lib/sites";
import { PriceTrendChart } from "./PriceTrendChart";
import { eventMeta, formatPrice, formatTime, statusMeta } from "@/lib/format";
import type { EventListData, HistoryListData, PriceRecord } from "@/lib/types";

function KindTag({ kind }: { kind: string }) {
  const meta = eventMeta(kind);
  return <ToneTag tone={meta.tone}>{meta.label}</ToneTag>;
}

interface ChangeLike {
  current?: { input_price: number | null; output_price: number | null; unit?: string } | null;
  previous?: { input_price: number | null; output_price: number | null; unit?: string } | null;
}

function describeChange(event: ChangeLike): string {
  const cur = event.current
    ? `${formatPrice(event.current.input_price)} / ${formatPrice(event.current.output_price)} ${event.current.unit ?? ""}`
    : "无价格";
  const prev = event.previous
    ? `${formatPrice(event.previous.input_price)} / ${formatPrice(event.previous.output_price)} ${event.previous.unit ?? ""}`
    : "无记录";
  return `${prev} → ${cur}`;
}

function EventFeed({ events }: { events: import("@/lib/types").EventRow[] }) {
  if (events.length === 0) {
    return <Empty>还没有事件。价格出现新增或变化时会记录在这里</Empty>;
  }
  return (
    <div style={{ display: "grid", gap: 10 }}>
      {events
        .slice()
        .reverse()
        .map((event, index) => {
          const tone = eventMeta(event.kind).tone;
          return (
            <div
              key={`${event.site_id}:${event.model}:${event.detected_at}:${index}`}
              style={{
                display: "flex",
                gap: 14,
                padding: "14px 18px",
                border: "1px solid var(--border)",
                borderRadius: 10,
                background: "var(--panel-2)",
              }}
            >
              <span aria-hidden className={`side-dot dot-${tone}`} style={{ marginTop: 7 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                  <KindTag kind={event.kind} />
                  <RiskLink
                    href={getSiteInfo(event.site_id, event.current?.source_url ?? event.previous?.source_url).homepage}
                    variant="site"
                  >
                    <span className="mono">{event.site_id}</span>
                  </RiskLink>
                  <span className="mono" style={{ color: "var(--text-2)" }}>{event.model}</span>
                  <span className="mono" style={{ color: "var(--text-3)", fontSize: 12, marginLeft: "auto" }}>
                    {formatTime(event.detected_at)}
                  </span>
                </div>
                <div style={{ color: "var(--text-2)", fontSize: 13, marginTop: 4 }}>
                  <span className="mono">{describeChange(event)}</span>
                </div>
              </div>
            </div>
          );
        })}
    </div>
  );
}

export function HistoryView({ events, history }: { events: EventListData; history: HistoryListData }) {
  const [view, setView] = useState<"events" | "records">("events");

  const sites = useMemo(
    () => Array.from(new Set([...events.events.map((e) => e.site_id), ...history.records.map((r) => r.site_id)])),
    [events, history],
  );
  const [site, setSite] = useState<string>("all");

  const filteredEvents = site === "all" ? events.events : events.events.filter((e) => e.site_id === site);
  const filteredRecords = site === "all" ? history.records : history.records.filter((r) => r.site_id === site);

  const columns: DColumn<PriceRecord>[] = [
    {
      title: "站点",
      dataIndex: "site_id",
      width: 130,
      render: (v: string, row) => {
        const info = getSiteInfo(v, row.source_url);
        return (
          <RiskLink href={info.homepage || row.source_url} variant="site">
            <span className="mono">{info.name}</span>
          </RiskLink>
        );
      },
    },
    { title: "模型", dataIndex: "model", render: (v: string) => <span className="mono">{v}</span> },
    { title: "输入价", dataIndex: "input_price", align: "right", render: (v: number | null) => <span className="mono">{formatPrice(v)}</span> },
    { title: "输出价", dataIndex: "output_price", align: "right", render: (v: number | null) => <span className="mono">{formatPrice(v)}</span> },
    { title: "单位", dataIndex: "unit", width: 150, render: (v: string) => <span className="mono" style={{ fontSize: 12, color: "var(--text-2)" }}>{v}</span> },
    {
      title: "状态",
      dataIndex: "price_status",
      width: 120,
      render: (v: string) => {
        const meta = statusMeta(v);
        return <ToneTag tone={meta.tone}>{meta.label}</ToneTag>;
      },
    },
    { title: "采集时间", dataIndex: "captured_at", width: 160, render: (v: number) => <span className="mono" style={{ color: "var(--text-2)", fontSize: 13 }}>{formatTime(v)}</span> },
  ];

  return (
    <div className="section-gap rise-in" style={{ display: "grid", gap: 24 }}>
      <div className="panel" style={{ padding: "18px 22px 12px" }}>
        <PriceTrendChart records={history.records} />
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
        <Seg
          value={view}
          onChange={(value) => setView(value as "events" | "records")}
          options={[
            { value: "events", label: `事件（${events.total}）` },
            { value: "records", label: `采集记录（${history.total}）` },
          ]}
        />
        <Sel
          value={site}
          onChange={setSite}
          options={[{ value: "all", label: "全部站点" }, ...sites.map((s) => ({ value: s, label: s }))]}
        />
      </div>

      {view === "events" ? (
        <EventFeed events={filteredEvents} />
      ) : (
        <div className="panel" style={{ overflow: "hidden" }}>
          <DataTable<PriceRecord>
            rowKey={(row) => `${row.site_id}:${row.model}:${row.captured_at}`}
            columns={columns}
            rows={filteredRecords}
            pageSize={20}
            scrollX={860}
            empty="暂无记录"
          />
        </div>
      )}
    </div>
  );
}
