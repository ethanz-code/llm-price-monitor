/** 展示格式化；语义色映射只输出 tone 名，颜色由 CSS 变量（.tone-*）按主题渲染。 */

export type Tone = "green" | "blue" | "yellow" | "red" | "gray";

export const STATUS_META: Record<string, { label: string; tone: Tone }> = {
  confirmed: { label: "confirmed", tone: "green" },
  candidate: { label: "candidate", tone: "yellow" },
  rule_only: { label: "rule_only", tone: "blue" },
  unavailable: { label: "unavailable", tone: "red" },
};

export const EVENT_META: Record<string, { label: string; tone: Tone }> = {
  new: { label: "新增", tone: "blue" },
  changed: { label: "价格变化", tone: "yellow" },
  price_increased: { label: "涨价", tone: "red" },
  price_decreased: { label: "降价", tone: "green" },
  restored: { label: "恢复", tone: "green" },
  status_changed: { label: "状态变化", tone: "yellow" },
};

export function eventMeta(kind: string): { label: string; tone: Tone } {
  return EVENT_META[kind] ?? { label: kind, tone: "gray" };
}

export function statusMeta(status: string): { label: string; tone: Tone } {
  return STATUS_META[status] ?? { label: status, tone: "gray" };
}

export function formatPrice(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const digits = value >= 100 ? 1 : value >= 1 ? 2 : 3;
  let text = value.toFixed(digits);
  if (text.includes(".")) text = text.replace(/0+$/, "").replace(/\.$/, "");
  return text;
}

export function formatTime(ts: number | undefined | null): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 折扣 = 站点价 / 官方价；0.21 显示为 21%，越小越便宜。 */
export function formatDiscount(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value * 100)}%`;
}

export function discountTone(value: number | null | undefined): Tone {
  if (value === null || value === undefined) return "gray";
  if (value <= 0.5) return "green";
  if (value <= 0.8) return "yellow";
  return "red";
}
