"use client";

/** 事件详情弹窗：价格事件展示前后完整价格与采集信息，公告事件展示全文。 */

import { Modal } from "./ui";
import { ToneTag } from "./ToneTag";
import { RiskLink } from "./RiskLink";
import { NoticeBody } from "./NoticeBody";
import { getSiteInfo } from "@/lib/sites";
import {
  currencySymbol,
  eventMeta,
  formatPrice,
  formatTime,
  isNoticeEvent,
  recordStatusKey,
  statusMeta,
  tieredPriceText,
  toCnyPrice,
} from "@/lib/format";
import type { FeedEvent, PriceRecord } from "@/lib/types";

interface ChangeLike {
  current?: { input_price: number | null; output_price: number | null; unit?: string; metadata?: PriceRecord["metadata"] } | null;
  previous?: { input_price: number | null; output_price: number | null; unit?: string; metadata?: PriceRecord["metadata"] } | null;
}

/** 事件摘要（前后价格一行带出），卡片与详情弹窗共用。 */
export function describeChange(event: ChangeLike, rate?: number | null): string {
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

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", gap: 12, alignItems: "baseline" }}>
      <span style={{ color: "var(--text-3)", fontSize: 12, flexShrink: 0, width: 64 }}>{label}</span>
      <span className="mono" style={{ color: "var(--text-1)", fontSize: 13, minWidth: 0, wordBreak: "break-all" }}>
        {children}
      </span>
    </div>
  );
}

/** 单条价格记录的完整信息：价格 + 采集来源等上下文。empty 为该侧无记录时的说明文案。 */
function RecordBlock({
  title,
  record,
  rate,
  empty,
}: {
  title: string;
  record: PriceRecord | null | undefined;
  rate?: number | null;
  empty: string;
}) {
  if (!record) {
    return (
      <div style={{ display: "grid", gap: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-2)" }}>{title}</div>
        <div style={{ color: "var(--text-3)", fontSize: 13 }}>{empty}</div>
      </div>
    );
  }
  const unit = record.unit;
  const converted = toCnyPrice(record.input_price, unit, rate) !== null;
  const symbol = converted ? "¥" : currencySymbol(unit);
  const price = (field: "input_price" | "output_price") => {
    const tierText = tieredPriceText(record, field, rate);
    if (tierText) return tierText;
    const value = converted ? toCnyPrice(record[field], unit, rate) : record[field];
    return `${symbol}${formatPrice(value ?? null)}`;
  };
  const status = statusMeta(recordStatusKey(record));
  const group = record.metadata?.group;
  const confidence = record.metadata?.confidence;
  const notes = record.metadata?.notes;
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-2)" }}>{title}</div>
      <Row label="输入价">{price("input_price")}</Row>
      <Row label="输出价">{price("output_price")}</Row>
      {unit && <Row label="计价单位">{unit}</Row>}
      <Row label="价格状态">
        <ToneTag tone={status.tone}>{status.label}</ToneTag>
      </Row>
      {group && <Row label="分组">{group}</Row>}
      {typeof confidence === "number" && <Row label="置信度">{Math.round(confidence * 100)}%</Row>}
      {notes && <Row label="备注">{String(notes)}</Row>}
      <Row label="采集时间">{formatTime(record.captured_at)}</Row>
      <Row label="数据来源">
        <RiskLink href={record.source_url} variant="muted">
          查看原始页面
        </RiskLink>
      </Row>
    </div>
  );
}

/** 详情弹窗：内容按事件类型区分；关闭后由 Modal 播完退出动画。 */
export function EventDetailModal({
  event,
  rate,
  onClose,
}: {
  event: FeedEvent | null;
  /** 展示汇率（USD→CNY），价格折算用 */
  rate?: number | null;
  onClose: () => void;
}) {
  if (!event) return null;
  const meta = eventMeta(event.kind);
  const site = getSiteInfo(event.site_id);
  if (isNoticeEvent(event)) {
    return (
      <Modal open onClose={onClose} title="事件详情" width={520}>
        <div style={{ display: "grid", gap: 14 }}>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <ToneTag tone={meta.tone}>{meta.label}</ToneTag>
            <RiskLink href={site.homepage} variant="site">
              {site.name || event.site_id}
            </RiskLink>
            <span className="mono" style={{ color: "var(--text-3)", fontSize: 12, marginLeft: "auto" }}>
              {formatTime(event.detected_at)}
            </span>
          </div>
          <NoticeBody
            content={event.content}
            style={{
              maxHeight: 360,
              overflowY: "auto",
              padding: 12,
              border: "1px solid var(--border)",
              borderRadius: 10,
              background: "var(--panel-2)",
            }}
          />
        </div>
      </Modal>
    );
  }
  return (
    <Modal open onClose={onClose} title="事件详情" width={520}>
      <div style={{ display: "grid", gap: 14 }}>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <ToneTag tone={meta.tone}>{meta.label}</ToneTag>
          <RiskLink href={site.homepage} variant="site">
            {site.name || event.site_id}
          </RiskLink>
          <span className="mono" style={{ color: "var(--text-2)" }}>{event.model}</span>
          <span className="mono" style={{ color: "var(--text-3)", fontSize: 12, marginLeft: "auto" }}>
            {formatTime(event.detected_at)}
          </span>
        </div>
        <div className="mono" style={{ fontSize: 13, color: "var(--text-2)", padding: "8px 12px", border: "1px solid var(--border)", borderRadius: 10 }}>
          {describeChange(event, rate)}
        </div>
        <div style={{ display: "grid", gap: 14 }}>
          <RecordBlock title="变化前" record={event.previous} rate={rate} empty="无前值（首次建档）" />
          <RecordBlock title="变化后" record={event.current} rate={rate} empty="无记录（模型可能已下线或未取到价）" />
        </div>
      </div>
    </Modal>
  );
}
