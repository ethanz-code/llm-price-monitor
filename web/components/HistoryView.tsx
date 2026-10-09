"use client";

import { useMemo, useState } from "react";
import { Empty, Pick } from "./ui";
import { IconSync } from "./icons";
import { ToneTag } from "./ToneTag";
import { RiskLink } from "./RiskLink";
import { getSiteInfo } from "@/lib/sites";
import type { EventRow } from "@/lib/types";
import { dayKey, dayLabel, eventMeta, formatTime, isNoticeEvent, noticeExcerpt, noticePlainText } from "@/lib/format";
import { foldEvents } from "@/lib/events";
import type { FeedEvent, HistoryListData } from "@/lib/types";
import { describeChange, EventDetailModal } from "./EventDetailModal";

function KindTag({ kind }: { kind: string }) {
  const meta = eventMeta(kind);
  return <ToneTag tone={meta.tone}>{meta.label}</ToneTag>;
}

/** 卡片上的小圆角按钮："详情""N 个分组"共用。 */
const pillStyle: React.CSSProperties = {
  border: "1px solid var(--border)",
  borderRadius: 999,
  background: "transparent",
  color: "var(--text-2)",
  fontSize: 12,
  padding: "1px 10px",
  cursor: "pointer",
};

function EventFeed({ events, rate }: { events: FeedEvent[]; rate?: number | null }) {
  const [openKeys, setOpenKeys] = useState<Set<string>>(new Set());
  const [detail, setDetail] = useState<FeedEvent | null>(null);
  if (events.length === 0) {
    return (
      <Empty
        icon={<IconSync size={18} />}
        title="还没有事件"
        description="价格或公告一有变化，我们都会记在这里。"
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
  // 分组卡片再按天分节：日期标题制造扫读节奏，几百张卡不再一路平铺
  const sections: { day: string; groups: FeedEvent[][] }[] = [];
  for (const group of foldEvents(events)) {
    const day = dayKey(group[0].detected_at);
    const last = sections[sections.length - 1];
    if (last && last.day === day) last.groups.push(group);
    else sections.push({ day, groups: [group] });
  }
  return (
    <div style={{ display: "grid", gap: 26 }}>
      {/* feed 已按 detected_at 倒序，直接分组渲染 = 最新在前，与首页「最新事件」一致 */}
      {sections.map((section) => (
      <section key={section.day} style={{ display: "grid", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ fontSize: 12.5, fontWeight: 550, color: "var(--text-3)" }}>
            {dayLabel(section.groups[0][0].detected_at)}
          </span>
          <span aria-hidden style={{ flex: 1, height: 1, background: "var(--border)" }} />
          <span className="mono" style={{ fontSize: 12, color: "var(--text-3)" }}>
            {section.groups.reduce((n, g) => n + g.length, 0)} 条
          </span>
        </div>
        {section.groups.map((group) => {
        const event = group[0];
        if (isNoticeEvent(event)) {
          const meta = eventMeta(event.kind);
          return (
            <div
              key={`${event.site_id}:${event.kind}:${event.detected_at}`}
              className="event-card"
              style={{ cursor: "pointer" }}
              onClick={() => setDetail(event)}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                  <KindTag kind={event.kind} />
                  <span onClick={(e) => e.stopPropagation()}>
                    <RiskLink href={getSiteInfo(event.site_id).homepage} variant="site">
                      <span>{getSiteInfo(event.site_id).name}</span>
                    </RiskLink>
                  </span>
                  <span className="mono" style={{ color: "var(--text-3)", fontSize: 12, marginLeft: "auto" }}>
                    {formatTime(event.detected_at)}
                  </span>
                </div>
                <div style={{ color: "var(--text-2)", fontSize: 13, marginTop: 4 }}>
                  <span title={noticePlainText(event.content)}>{noticeExcerpt(event.content)}</span>
                </div>
              </div>
            </div>
          );
        }
        const key = `${event.site_id}:${event.model}:${event.kind}:${event.detected_at}`;
        const open = openKeys.has(key);
        return (
          <div key={key} className="event-card" style={{ cursor: "pointer" }} onClick={() => setDetail(event)}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                <KindTag kind={event.kind} />
                <span onClick={(e) => e.stopPropagation()}>
                  <RiskLink
                    href={getSiteInfo(event.site_id, event.current?.source_url ?? event.previous?.source_url).homepage}
                    variant="site"
                  >
                    <span>{getSiteInfo(event.site_id, event.current?.source_url ?? event.previous?.source_url).name}</span>
                  </RiskLink>
                </span>
                <span className="mono" style={{ color: "var(--text-2)" }}>{event.model}</span>
                {group.length > 1 ? (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      toggle(key);
                    }}
                    style={pillStyle}
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
      </section>
      ))}
      <EventDetailModal event={detail} rate={rate} onClose={() => setDetail(null)} />
    </div>
  );
}

export function HistoryView({
  feed,
  history,
}: {
  /** 价格事件与公告事件已按时间合并、按新到旧排序的动态流 */
  feed: FeedEvent[];
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
    <div className="rise-in" style={{ display: "grid", gap: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
        <span style={{ fontWeight: 550, fontSize: 15 }}>事件</span>
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
