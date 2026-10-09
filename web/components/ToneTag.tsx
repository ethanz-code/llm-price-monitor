import type { ReactNode } from "react";

export type Tone = "green" | "blue" | "yellow" | "red" | "gray";

/** 语义色标签：颜色由 CSS 变量（.tone-*）驱动，双主题自动适配。 */
export function ToneTag({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`tag tone-${tone}`}>{children}</span>;
}

/** 数值/事实的着色文本：折扣、免费这类数据信息用带色文字表达，不做底色贴纸，
 *  贴纸只留给状态语义（需登录、不可用、事件类型）。 */
export function ToneNum({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className="mono" style={{ color: `var(--tone-${tone}-text)`, fontSize: 12.5 }}>
      {children}
    </span>
  );
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
