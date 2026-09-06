import { theme as antdTheme } from "antd";
import type { ThemeConfig } from "antd";

export type ThemeMode = "light" | "dark" | "system";

/**
 * 免闪烁主题引导：内联在 <head>，先于首次绘制按 localStorage/系统偏好
 * 设置 <html data-theme> 与 colorScheme。与 providers.tsx 的运行时逻辑保持一致。
 */
export const themeInitScript = `(function(){try{var m=localStorage.getItem("theme-mode")||"system";var d=m==="dark"||(m==="system"&&window.matchMedia("(prefers-color-scheme: dark)").matches);var t=d?"dark":"light";document.documentElement.dataset.theme=t;document.documentElement.style.colorScheme=t;}catch(e){}})();`;

export function resolveDark(mode: ThemeMode): boolean {
  if (mode === "dark") return true;
  if (mode === "light") return false;
  if (typeof window === "undefined") return true; // SSR 默认暗色（dark-first）
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

// 设计基调取自同类站实测（openrouter.ai / llm-stats.com / benchlm.ai / vellum.ai）：
// 近黑冷底、1px 半透明描边、6px 控件圆角、荧光绿品牌色克制点缀、等宽数字
export function buildTheme(dark: boolean): ThemeConfig {
  return {
    algorithm: dark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
    token: {
      // 主色为荧光绿（OpenRouter 同路）：实底 CTA + 深色内容，双主题一致。
      // colorTextLightSolid 同步为深色，否则主按钮/勾选框等"实底上的内容"
      // 会用默认浅色画在亮绿底上。
      colorPrimary: "#C8FF00",
      colorPrimaryHover: dark ? "#E0FF54" : "#D9FF45",
      colorPrimaryActive: dark ? "#B4D900" : "#B7E300",
      colorTextLightSolid: "#101418",
      colorInfo: dark ? "#5B9BFF" : "#2F6FED",
      colorLink: dark ? "#C8FF00" : "#3F5600",
      colorBgContainer: dark ? "#121518" : "#FFFFFF",
      colorBgElevated: dark ? "#1B1F24" : "#FFFFFF",
      colorBorder: dark ? "rgba(255,255,255,0.14)" : "rgba(0,0,0,0.12)",
      colorBorderSecondary: dark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)",
      colorText: dark ? "#E6E8EC" : "#2F3437",
      colorTextSecondary: dark ? "#9AA0A8" : "#787774",
      colorTextTertiary: dark ? "#6A7076" : "#9B9B98",
      borderRadius: 6,
      fontFamily:
        '-apple-system, "SF Pro Display", "Helvetica Neue", "PingFang SC", "Segoe UI", sans-serif',
      fontSize: 14,
      boxShadow: "none",
      boxShadowSecondary: dark ? "0 4px 24px rgba(0,0,0,0.4)" : "0 2px 12px rgba(0,0,0,0.06)",
    },
    components: {
      // 反白按钮细节：字重 500、去默认投影；暗色下次级按钮用半透明白底（对齐 OpenRouter 幽灵按钮）
      Button: {
        fontWeight: 500,
        primaryShadow: "none",
        defaultShadow: "none",
        ...(dark
          ? { defaultBg: "rgba(255,255,255,0.04)", defaultBorderColor: "rgba(255,255,255,0.14)" }
          : {}),
      },
      Menu: {
        itemBg: "transparent",
        activeBarBorderWidth: 0,
        itemSelectedBg: dark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.05)",
        itemSelectedColor: dark ? "#C8FF00" : "#3F5600",
        itemColor: dark ? "#9DA3A6" : "#787774",
        itemHoverBg: "transparent",
        itemHoverColor: dark ? "#E8E6E3" : "#2F3437",
      },
      Table: {
        headerBg: dark ? "rgba(255,255,255,0.02)" : "#FAFAF8",
        headerColor: dark ? "#9DA3A6" : "#787774",
        rowHoverBg: dark ? "rgba(255,255,255,0.03)" : "rgba(0,0,0,0.02)",
        cellPaddingBlockMD: 14,
        borderColor: dark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.06)",
      },
      Card: { paddingLG: 24 },
      Tooltip: { colorBgSpotlight: dark ? "#23282E" : "#26282B" },
      Segmented: {
        itemSelectedBg: dark ? "rgba(255,255,255,0.08)" : "#FFFFFF",
        trackBg: dark ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.04)",
      },
      Modal: { contentBg: dark ? "#14171A" : "#FFFFFF", headerBg: dark ? "#14171A" : "#FFFFFF" },
    },
  };
}
