"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { resolveDark, type ThemeMode } from "@/theme";

interface ThemeContextValue {
  mode: ThemeMode;
  dark: boolean;
  setMode: (mode: ThemeMode) => void;
}

const ThemeContext = createContext<ThemeContextValue>({
  mode: "system",
  dark: true,
  setMode: () => {},
});

export function useTheme() {
  return useContext(ThemeContext);
}

/** 主题上下文：免闪烁引导（themeInitScript）先行，这里只负责运行时切换。 */
export function Providers({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>("system");
  // 初值固定暗色与服务端一致（SSR 无 document，只能按 dark-first 渲染），避免浅色用户首帧渲染出的
  // 主题相关属性和服务端 HTML 对不上——水合不一致的属性 React 不会修补，会一直留在错的那套配色上。
  // 真实主题由下面的 apply() 在挂载后校正（浅色用户会因此多一次重渲染）。
  const [dark, setDark] = useState(true);

  const apply = useCallback(() => {
    const stored = (localStorage.getItem("theme-mode") as ThemeMode | null) ?? "system";
    const isDark = resolveDark(stored);
    setModeState(stored);
    setDark(isDark);
    const resolved = isDark ? "dark" : "light";
    document.documentElement.dataset.theme = resolved;
    document.documentElement.style.colorScheme = resolved;
  }, []);

  useEffect(() => {
    apply();
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", apply);
    window.addEventListener("theme-change", apply);
    return () => {
      media.removeEventListener("change", apply);
      window.removeEventListener("theme-change", apply);
    };
  }, [apply]);

  const value = useMemo<ThemeContextValue>(
    () => ({
      mode,
      dark,
      setMode: (next: ThemeMode) => {
        localStorage.setItem("theme-mode", next);
        window.dispatchEvent(new Event("theme-change"));
      },
    }),
    [mode, dark],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
