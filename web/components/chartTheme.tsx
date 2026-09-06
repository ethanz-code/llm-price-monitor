"use client";

import type { ReactNode } from "react";
import { useTheme } from "@/app/providers";

/** 图表多系列色板：浅色/深色各一套，系列与颜色按下标一一对应。
 *  亮色版取品牌色的中等明度变体：白底上够亮、不成片发黑。 */
export const PALETTE_LIGHT = ["#86C200", "#4A9BD6", "#D9A013", "#D95653", "#9DA3A6", "#A474D6"];
export const PALETTE_DARK = ["#C8FF00", "#5B9BFF", "#E0B45C", "#E27B78", "#9DA3A6", "#B08FFF"];

/** 全站统一的图表主题：坐标轴、网格、主色与多系列色板，随明暗主题切换。 */
export function useChartTheme() {
  const { dark } = useTheme();
  return {
    dark,
    axisColor: dark ? "#6E7478" : "#9B9B98",
    gridColor: dark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.06)",
    lineColor: dark ? "#C8FF00" : "#86C200",
    secondaryColor: dark ? "#9DA3A6" : "#ADACA8",
    palette: dark ? PALETTE_DARK : PALETTE_LIGHT,
  };
}

/** 统一的悬停气泡：深色底、mono 标题、可选彩色圆点条目；所有图表共用。 */
export function ChartBubble({
  label,
  rows,
}: {
  label?: ReactNode;
  rows: { color?: string; name: ReactNode; value: ReactNode }[];
}) {
  return (
    <div
      style={{
        background: "rgba(23, 26, 31, 0.96)",
        color: "rgba(255,255,255,0.92)",
        padding: "8px 12px",
        borderRadius: 6,
        fontSize: 12.5,
        lineHeight: 1.7,
        maxWidth: 340,
        boxShadow: "0 8px 24px rgba(0, 0, 0, 0.25)",
      }}
    >
      {label != null && label !== "" && (
        <div className="mono" style={{ marginBottom: rows.length ? 2 : 0 }}>
          {label}
        </div>
      )}
      {rows.map((row, index) => (
        <div key={index} style={{ display: "flex", gap: 6, alignItems: "center" }}>
          {row.color && <span aria-hidden style={{ width: 8, height: 8, borderRadius: 2, background: row.color, flexShrink: 0 }} />}
          <span className="mono">{row.name}</span>
          <span>{row.value}</span>
        </div>
      ))}
    </div>
  );
}
