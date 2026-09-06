"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { Btn } from "./ui";
import { IconCheck, IconClose, IconMenu, IconMonitor, IconMoon, IconSun } from "./icons";
import { LogoMark } from "./LogoMark";
import { useTheme } from "@/app/providers";
import type { ThemeMode } from "@/theme";

const ITEMS = [
  { key: "/", label: "首页" },
  { key: "/overview", label: "价格总览" },
  { key: "/history", label: "历史与事件" },
  { key: "/official", label: "官方价库" },
  { key: "/discount", label: "折扣对比" },
];

const MODE_OPTIONS: { value: ThemeMode; label: string; title: string; icon: React.ReactNode }[] = [
  { value: "light", label: "浅色", title: "浅色模式", icon: <IconSun size={14} /> },
  { value: "dark", label: "深色", title: "深色模式", icon: <IconMoon size={14} /> },
  { value: "system", label: "系统", title: "跟随系统", icon: <IconMonitor size={14} /> },
];

/** 三档主题分段控件：图标 + 选中高亮，当前档位一眼可见。 */
function ThemeSegmented() {
  const { mode, setMode } = useTheme();
  return (
    <span className="seg theme-seg" role="radiogroup" aria-label="主题模式">
      {MODE_OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={mode === option.value}
          title={option.title}
          aria-label={option.title}
          className={`seg-item${mode === option.value ? " on" : ""}`}
          onClick={() => setMode(option.value)}
        >
          {option.icon}
          <span className="theme-seg-label">{option.label}</span>
        </button>
      ))}
    </span>
  );
}

/** 透明浮动导航：logo 胶囊居左，菜单与主题切换靠右。
 *  管理入口不放在导航里——见页脚的低调链接。 */
export function SiteNav() {
  const pathname = usePathname();
  const selected = `/${pathname.split("/")[1] ?? ""}`;
  const { mode, setMode } = useTheme();
  const [scrolled, setScrolled] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) setMenuOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  return (
    <header className="site-header" data-scrolled={scrolled ? "true" : undefined}>
      <div className="nav-inner">
        <Link href="/" className="brand-pill">
          <LogoMark size={24} />
          <span className="brand-name">LLM 价格监控</span>
          <span className="mono brand-version">v0.1</span>
        </Link>

        <nav className="nav-pill" aria-label="主导航">
          {ITEMS.map((item) => (
            <Link
              key={item.key}
              href={item.key}
              className={`nav-pill-item${selected === item.key ? " active" : ""}`}
            >
              {item.label}
            </Link>
          ))}
        </nav>

        <span className="nav-mobile-wrap">
          <Btn
            variant="ghost"
            className="nav-mobile-menu"
            ariaLabel="打开导航菜单"
            onClick={() => setMenuOpen((value) => !value)}
          >
            {menuOpen ? <IconClose size={16} /> : <IconMenu size={16} />}
          </Btn>
          {menuOpen && (
            <div className="nav-pop" ref={menuRef}>
              <nav style={{ display: "grid", gap: 2 }}>
                {ITEMS.map((item) => (
                  <Link
                    key={item.key}
                    href={item.key}
                    onClick={() => setMenuOpen(false)}
                    className={`nav-pop-item${selected === item.key ? " active" : ""}`}
                  >
                    {item.label}
                    {selected === item.key && <IconCheck size={13} />}
                  </Link>
                ))}
              </nav>
              <div className="nav-pop-theme">
                <span className="admin-rail-title">主题</span>
                <div style={{ display: "flex", gap: 6 }}>
                  {MODE_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      className={`nav-pop-theme-item${mode === option.value ? " active" : ""}`}
                      onClick={() => setMode(option.value)}
                    >
                      {option.icon}
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}
        </span>

        <div className="nav-actions">
          <ThemeSegmented />
        </div>
      </div>
    </header>
  );
}
