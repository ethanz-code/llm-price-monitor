"use client";

import { useMemo, useState } from "react";
import { Empty, Pick } from "./ui";
import { IconSync } from "./icons";
import { ToneTag } from "./ToneTag";
import { RiskLink } from "./RiskLink";
import { getSiteInfo } from "@/lib/sites";
import type { EventRow } from "@/lib/types";
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
import type { FeedEvent, HistoryListData, PriceRecord } from "@/lib/types";

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
  if (!previous) return `新增 ${current}${suffix}`;
  return `${previous} → ${current}${suffix}`;
}

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
  feed,
  history,
  priceTotal,
  noticeTotal = 0,
}: {
  /** 价格事件与公告事件已按时间合并、按新到旧排序的动态流 */
  feed: FeedEvent[];
  /** 价格事件全量总数（feed 可能被 limit 截断，计数用它才准确） */
  priceTotal: number;
  /** 公告事件全量总数 */
  noticeTotal?: number;
  /** 历史接口数据，主要用于取汇率做价格折算 */
  history: HistoryListData;
}) {
  // 站点价统一按 RMB 展示：汇率由历史接口附带（厂商价快照口径）
  const rate = history.rate ?? null;

  const sites = useMemo(
    () =>
      Array.from(
        new Set([
          ...feed.map((e) => e.site_id),
          ...history.records.map((r) => r.site_id),
        ]),
      ),
    [feed, history],
  );
  const [site, setSite] = useState<string>("all");

  const filteredEvents = site === "all" ? feed : feed.filter((e) => e.site_id === site);

  return (
    <div className="section-gap rise-in" style={{ display: "grid", gap: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
        <span style={{ fontWeight: 550, fontSize: 15 }}>事件（{priceTotal + noticeTotal}）</span>
        <Pick
          value={site}
          onChange={setSite}
          options={[{ value: "all", label: "全部站点" }, ...sites.map((s) => ({ value: s, label: getSiteInfo(s).name || s }))]}
        />
      </div>

      <EventFeed events={filteredEvents} rate={rate} />
    </div>
  );
}
