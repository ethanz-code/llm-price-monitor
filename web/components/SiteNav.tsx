"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { Btn } from "./ui";
import {
  IconCheck,
  IconClose,
  IconMenu,
  IconMonitor,
  IconMoon,
  IconSun,
} from "./icons";
import { LogoMark } from "./LogoMark";
import { useTheme } from "@/app/providers";
import { fetchAuthState } from "@/lib/api";
import { nav, site } from "@/lib/copy";
import type { ThemeMode } from "@/theme";

const ITEMS = nav.items;

const ADMIN_ITEM = { key: "/admin", label: nav.adminLabel };

const MODE_OPTIONS: {
  value: ThemeMode;
  label: string;
  title: string;
  icon: React.ReactNode;
}[] = [
  {
    value: "light",
    label: nav.theme.light.label,
    title: nav.theme.light.title,
    icon: <IconSun size={14} />,
  },
  {
    value: "dark",
    label: nav.theme.dark.label,
    title: nav.theme.dark.title,
    icon: <IconMoon size={14} />,
  },
  {
    value: "system",
    label: nav.theme.system.label,
    title: nav.theme.system.title,
    icon: <IconMonitor size={14} />,
  },
];

/** 桌面主题切换：单图标触发器 + 下拉三选，当前档位由图标本身表达。 */
function ThemeMenu() {
  const { mode, setMode } = useTheme();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: PointerEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node))
        setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    // pointerdown 覆盖触屏长按/滚动等 mousedown 缺席的场景
    window.addEventListener("pointerdown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const current =
    MODE_OPTIONS.find((option) => option.value === mode) ?? MODE_OPTIONS[2];
  return (
    <span className="theme-wrap" ref={ref}>
      <button
        type="button"
        className="theme-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`主题模式：${current.title}`}
        title={`主题：${current.title}`}
        onClick={() => setOpen((value) => !value)}
      >
        {current.icon}
      </button>
      {open && (
        <div className="nav-pop theme-pop" role="menu">
          {MODE_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              role="menuitemradio"
              aria-checked={mode === option.value}
              className={`nav-pop-theme-item${mode === option.value ? " active" : ""}`}
              onClick={() => {
                setMode(option.value);
                setOpen(false);
              }}
            >
              {option.icon}
              {option.label}
            </button>
          ))}
        </div>
      )}
    </span>
  );
}

/** 透明浮动导航：logo 胶囊居左，菜单与主题切换靠右；管理员登录后追加"管理"入口。 */
export function SiteNav() {
  const pathname = usePathname();
  const selected = `/${pathname.split("/")[1] ?? ""}`;
  const { mode, setMode } = useTheme();
  const [isAdmin, setIsAdmin] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const [hidden, setHidden] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    fetchAuthState().then((state) => {
      if (alive && state?.is_admin) setIsAdmin(true);
    });
    return () => {
      alive = false;
    };
  }, []);

  const items = isAdmin ? [...ITEMS, ADMIN_ITEM] : ITEMS;

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // 下滑藏起、上滑滑回:滚过约 80px 后下滑才藏,顶部一段距离内始终显示。
  // 依赖 pathname:路由切换时重挂 effect 重置 lastY,避免跨页面残留旧基准;1px 死区过滤惯性抖动
  useEffect(() => {
    let lastY = window.scrollY;
    let raf = 0;
    const onScroll = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        const y = window.scrollY;
        if (y <= 80) setHidden(false);
        else if (y > lastY + 1) setHidden(true);
        else if (y < lastY - 1) setHidden(false);
        lastY = y;
      });
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [pathname]);

  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (event: PointerEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node))
        setMenuOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    // pointerdown 覆盖触屏长按/滚动等 mousedown 缺席的场景
    window.addEventListener("pointerdown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  return (
    <header
      className="site-header"
      data-scrolled={scrolled ? "true" : undefined}
      data-hidden={hidden ? "true" : undefined}
    >
      <div className="nav-inner">
        <Link href="/" className="brand-pill">
          <LogoMark size={24} />
          <span className="brand-name">{site.name}</span>
        </Link>

        <nav className="nav-pill" aria-label="主导航">
          {items.map((item) => (
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
                {items.map((item) => (
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
          <ThemeMenu />
        </div>
      </div>
    </header>
  );
}
