"use client";

import { useMemo, useState } from "react";
import { DataTable, type DColumn } from "./DataTable";
import { Empty, Sel, Seg } from "./ui";
import { IconSync } from "./icons";
import { ToneTag } from "./ToneTag";
import { RiskLink } from "./RiskLink";
import { TermTip } from "./TermTip";
import { getSiteInfo } from "@/lib/sites";
import { PriceTrendChart } from "./PriceTrendChart";
import {
  currencySymbol,
  eventMeta,
  formatPrice,
  formatTime,
  isNoticeEvent,
  noticeExcerpt,
  tieredPriceText,
  toCnyPrice,
} from "@/lib/format";
import { PriceCell } from "./PriceCell";
import type { EventListData, EventRow, HistoryListData, NoticeEvent, PriceRecord } from "@/lib/types";

function KindTag({ kind }: { kind: string }) {
  const meta = eventMeta(kind);
  return <ToneTag tone={meta.tone}>{meta.label}</ToneTag>;
}

interface ChangeLike {
  current?: { input_price: number | null; output_price: number | null; unit?: string; metadata?: PriceRecord["metadata"] } | null;
  previous?: { input_price: number | null; output_price: number | null; unit?: string; metadata?: PriceRecord["metadata"] } | null;
}

function describeChange(event: ChangeLike, rate?: number | null): string {
  const unit = event.current?.unit ?? event.previous?.unit ?? "";
  // 事件摘要统一按 RMB 展示：USD 价乘汇率折算，无法折算回落原币
  const converted = toCnyPrice(event.current?.input_price, unit, rate) !== null;
  const symbol = converted ? "¥" : currencySymbol(unit);
  const price = (record: ChangeLike["current"], field: "input_price" | "output_price") => {
    const tierText = tieredPriceText(record, field, rate);
    if (tierText) return tierText;
    const value = converted ? toCnyPrice(record?.[field], unit, rate) : record?.[field];
    return `${symbol}${formatPrice(value ?? null)}`;
  };
  const pair = (record: ChangeLike["current"]) =>
    record ? `${price(record, "input_price")} / ${price(record, "output_price")}` : null;
  const suffix = unit ? ` · ${converted ? "CNY/1M tokens（折算）" : unit}` : "";
  const current = pair(event.current) ?? "无价格";
  const previous = pair(event.previous);
  if (!previous) return `首次记录 ${current}${suffix}`;
  return `${previous} → ${current}${suffix}`;
}

/** 事件流条目：价格事件（带模型与前后价格）或公告事件（带公告正文）。 */
type FeedEvent = EventRow | NoticeEvent;

// 同一轮扫描（检测时间相邻 120 秒内）同站点+模型+同类事件折成一组，多分组只显示一张卡；公告事件不折叠
function foldEvents(events: FeedEvent[]): FeedEvent[][] {
  const groups: FeedEvent[][] = [];
  for (const event of events) {
    const last = groups[groups.length - 1];
    const lastEvent = last?.[last.length - 1];
    if (
      last &&
      lastEvent &&
      !isNoticeEvent(event) &&
      !isNoticeEvent(lastEvent) &&
      lastEvent.site_id === event.site_id &&
      lastEvent.model === event.model &&
      lastEvent.kind === event.kind &&
      Math.abs(event.detected_at - lastEvent.detected_at) <= 120
    ) {
      last.push(event);
    } else {
      groups.push([event]);
    }
  }
  return groups;
}

function EventFeed({ events, rate }: { events: FeedEvent[]; rate?: number | null }) {
  const [openKeys, setOpenKeys] = useState<Set<string>>(new Set());
  if (events.length === 0) {
    return (
      <Empty
        icon={<IconSync size={18} />}
        title="还没有事件"
        description="价格或公告出现变化时会记录在这里。"
      />
    );
  }
  const toggle = (key: string) =>
    setOpenKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  return (
    <div style={{ display: "grid", gap: 10 }}>
      {/* feed 已按 detected_at 倒序，直接分组渲染 = 最新在前，与首页「最新事件」一致 */}
      {foldEvents(events).map((group) => {
        const event = group[0];
        if (isNoticeEvent(event)) {
          const meta = eventMeta(event.kind);
          return (
            <div key={`${event.site_id}:${event.kind}:${event.detected_at}`} className="event-card">
              <span aria-hidden className={`side-dot dot-${meta.tone}`} style={{ marginTop: 7 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                  <KindTag kind={event.kind} />
                  <RiskLink href={getSiteInfo(event.site_id).homepage} variant="site">
                    <span className="mono">{event.site_id}</span>
                  </RiskLink>
                  <span className="mono" style={{ color: "var(--text-3)", fontSize: 12, marginLeft: "auto" }}>
                    {formatTime(event.detected_at)}
                  </span>
                </div>
                <div style={{ color: "var(--text-2)", fontSize: 13, marginTop: 4 }}>
                  <span title={event.content}>{noticeExcerpt(event.content)}</span>
                </div>
              </div>
            </div>
          );
        }
        const tone = eventMeta(event.kind).tone;
        const key = `${event.site_id}:${event.model}:${event.kind}:${event.detected_at}`;
        const open = openKeys.has(key);
        return (
          <div key={key} className="event-card">
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
                {group.length > 1 ? (
                  <button
                    type="button"
                    onClick={() => toggle(key)}
                    style={{
                      border: "1px solid var(--border)",
                      borderRadius: 999,
                      background: "transparent",
                      color: "var(--text-2)",
                      fontSize: 12,
                      padding: "1px 10px",
                      cursor: "pointer",
                    }}
                  >
                    {open ? "收起" : `${group.length} 个分组`}
                  </button>
                ) : (
                  (event.current?.metadata?.group || event.previous?.metadata?.group) && (
                    <span className="mono" style={{ color: "var(--text-3)" }}>
                      {event.current?.metadata?.group || event.previous?.metadata?.group}
                    </span>
                  )
                )}
                <span className="mono" style={{ color: "var(--text-3)", fontSize: 12, marginLeft: "auto" }}>
                  {formatTime(event.detected_at)}
                </span>
              </div>
              {group.length === 1 ? (
                <div style={{ color: "var(--text-2)", fontSize: 13, marginTop: 4 }}>
                  <span className="mono">{describeChange(event, rate)}</span>
                </div>
              ) : (
                <div style={{ marginTop: 4, display: "grid", gap: 4 }}>
                  {(open ? group : group.slice(0, 1))
                    .filter((item): item is EventRow => !isNoticeEvent(item))
                    .map((item, itemIndex) => {
                      const groupLabel = item.current?.metadata?.group || item.previous?.metadata?.group || "default";
                      return (
                        <div key={`${groupLabel}:${itemIndex}`} style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
                          <span className="mono" style={{ color: "var(--text-3)", fontSize: 12, flexShrink: 0 }}>
                            {groupLabel}
                          </span>
                          <span className="mono" style={{ color: "var(--text-2)", fontSize: 13 }}>{describeChange(item, rate)}</span>
                        </div>
                      );
                    })}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function HistoryView({
  events,
  history,
  noticeEvents = [],
  noticeTotal = 0,
}: {
  events: EventListData;
  history: HistoryListData;
  noticeEvents?: NoticeEvent[];
  /** 公告事件全量总数（noticeEvents 可能被 limit 截断，计数用它才准确） */
  noticeTotal?: number;
}) {
  const [view, setView] = useState<"events" | "records">("events");
  // 站点价统一按 RMB 展示：汇率由事件/历史接口附带（厂商价快照口径）
  const rate = events.rate ?? history.rate ?? null;

  // 价格事件与公告事件合并成一条时间线（公告事件带 content，渲染走独立分支）
  const feed = useMemo(
    () => [...events.events, ...noticeEvents].sort((a, b) => b.detected_at - a.detected_at),
    [events, noticeEvents],
  );
  const sites = useMemo(
    () =>
      Array.from(
        new Set([
          ...events.events.map((e) => e.site_id),
          ...noticeEvents.map((e) => e.site_id),
          ...history.records.map((r) => r.site_id),
        ]),
      ),
    [events, history, noticeEvents],
  );
  const [site, setSite] = useState<string>("all");

  const filteredEvents = site === "all" ? feed : feed.filter((e) => e.site_id === site);
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
    { title: "模型", dataIndex: "model", width: 220, render: (v: string) => <span className="mono">{v}</span> },
    {
      title: (
        <>
          分组
          <TermTip term="group" />
        </>
      ),
      key: "group",
      width: 110,
      mobileHide: true,
      render: (_v, row) => <span className="mono" style={{ color: "var(--text-2)" }}>{row.metadata?.group || "—"}</span>,
    },
    {
      title: (
        <>
          输入价
          <TermTip term="input_price" />
        </>
      ),
      dataIndex: "input_price",
      align: "right",
      width: 130,
      render: (_v: number | null, row) => <PriceCell row={row} field="input_price" rate={rate} />,
    },
    {
      title: (
        <>
          输出价
          <TermTip term="output_price" />
        </>
      ),
      dataIndex: "output_price",
      align: "right",
      width: 130,
      render: (_v: number | null, row) => <PriceCell row={row} field="output_price" rate={rate} />,
    },
    {
      title: (
        <>
          单位
          <TermTip term="unit" />
        </>
      ),
      dataIndex: "unit",
      width: 140,
      mobileHide: true,
      render: (v: string) => <span className="mono" style={{ fontSize: 12, color: "var(--text-2)" }}>{v}</span>,
    },
    { title: "采集时间", dataIndex: "captured_at", width: 160, mobileHide: true, render: (v: number) => <span className="mono" style={{ color: "var(--text-2)", fontSize: 13, whiteSpace: "nowrap" }}>{formatTime(v)}</span> },
  ];

  return (
    <div className="section-gap rise-in" style={{ display: "grid", gap: 24 }}>
      <div className="panel" style={{ padding: "18px 22px 12px" }}>
        <PriceTrendChart records={history.records} rate={rate} />
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
        <Seg
          value={view}
          onChange={(value) => setView(value as "events" | "records")}
          options={[
            { value: "events", label: `事件（${events.total + noticeTotal}）` },
            { value: "records", label: `采集记录（${history.total}）` },
          ]}
        />
        <Sel
          value={site}
          onChange={setSite}
          options={[{ value: "all", label: "全部站点" }, ...sites.map((s) => ({ value: s, label: getSiteInfo(s).name || s }))]}
        />
      </div>

      {view === "events" ? (
        <EventFeed events={filteredEvents} rate={rate} />
      ) : (
        <div className="panel" style={{ overflow: "hidden" }}>
          <DataTable<PriceRecord>
            rowKey={(row) => `${row.site_id}:${row.model}:${row.captured_at}`}
            columns={columns}
            rows={filteredRecords}
            paginated
            scrollX={1020}
            mobileScrollX={610}
            empty="暂无记录"
          />
        </div>
      )}
    </div>
  );
}
