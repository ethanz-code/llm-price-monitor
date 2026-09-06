"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { App, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import type { ReactNode } from "react";
import { buildTheme, resolveDark, type ThemeMode } from "@/theme";

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

export function Providers({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>("system");
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

  return (
    <ThemeContext.Provider value={value}>
      <ConfigProvider locale={zhCN} theme={buildTheme(dark)}>
        <App>{children}</App>
      </ConfigProvider>
    </ThemeContext.Provider>
  );
}
