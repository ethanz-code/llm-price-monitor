import type { ReactNode } from "react";

export type Tone = "green" | "blue" | "yellow" | "red" | "gray";

/** 语义色标签：颜色由 CSS 变量（.tone-*）驱动，双主题自动适配。 */
export function ToneTag({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`tag tone-${tone}`}>{children}</span>;
}

/** 规则价低调标注：小号灰字，不与确认价抢视线；悬停解释可信度。 */
export function RulePriceMark({ tip }: { tip?: string }) {
  return (
    <span
      title={tip ?? "价格由 AI 从站点数据推算，未经页面交叉验证，仅供参考"}
      style={{ fontSize: 11.5, color: "var(--text-3)", flexShrink: 0 }}
    >
      规则价
    </span>
  );
}
