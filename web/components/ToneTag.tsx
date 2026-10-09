import type { ReactNode } from "react";

export type Tone = "green" | "blue" | "yellow" | "red" | "gray";

/** 语义色标签：颜色由 CSS 变量（.tone-*）驱动，双主题自动适配。 */
export function ToneTag({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`tag tone-${tone}`}>{children}</span>;
}
