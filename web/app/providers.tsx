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
  // 初值直接跟随 themeInitScript 已写入的 data-theme，避免浅色用户挂载后 dark 翻转触发下游重建
  const [dark, setDark] = useState(
    () => typeof document === "undefined" || document.documentElement.dataset.theme !== "light",
  );

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
