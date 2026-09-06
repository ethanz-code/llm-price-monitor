export type ThemeMode = "light" | "dark" | "system";

/**
 * 免闪烁主题引导：内联在 <head>，先于首次绘制按 localStorage/系统偏好
 * 设置 <html data-theme> 与 colorScheme。与 providers.tsx 的运行时逻辑保持一致。
 * 视觉全部由 globals.css 的 CSS 变量驱动，不再依赖组件库 token。
 */
export const themeInitScript = `(function(){try{var m=localStorage.getItem("theme-mode")||"system";var d=m==="dark"||(m==="system"&&window.matchMedia("(prefers-color-scheme: dark)").matches);var t=d?"dark":"light";document.documentElement.dataset.theme=t;document.documentElement.style.colorScheme=t;}catch(e){}})();`;

export function resolveDark(mode: ThemeMode): boolean {
  if (mode === "dark") return true;
  if (mode === "light") return false;
  if (typeof window === "undefined") return true; // SSR 默认暗色（dark-first）
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}
