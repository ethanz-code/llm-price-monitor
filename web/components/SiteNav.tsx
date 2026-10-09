"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import { Btn } from "./ui";
import { IconCheck, IconClose, IconMenu, IconMoon, IconSun } from "./icons";
import { LogoMark } from "./LogoMark";
import { useTheme } from "@/app/providers";
import type { MetaData } from "@/lib/types";
import type { ThemeMode } from "@/theme";

const ITEMS = [
  { key: "/", label: "首页" },
  { key: "/overview", label: "价格总览" },
  { key: "/history", label: "历史与事件" },
  { key: "/official", label: "官方价库" },
  { key: "/discount", label: "折扣对比" },
];

const MODE_OPTIONS: { value: ThemeMode; label: string; icon: React.ReactNode }[] = [
  { value: "light", label: "浅色", icon: <IconSun size={14} /> },
  { value: "dark", label: "深色", icon: <IconMoon size={14} /> },
  { value: "system", label: "跟随系统", icon: <span className="mono" style={{ fontSize: 10 }}>SYS</span> },
];

function ThemeToggle() {
  const { mode, setMode } = useTheme();
  const next = MODE_OPTIONS[(MODE_OPTIONS.findIndex((option) => option.value === mode) + 1) % MODE_OPTIONS.length];
  return (
    <button
      aria-label={`当前主题：${MODE_OPTIONS.find((option) => option.value === mode)?.label}，点击切换到${next.label}`}
      title={`主题：${MODE_OPTIONS.find((option) => option.value === mode)?.label}，切换到${next.label}`}
      className="icon-btn"
      onClick={() => setMode(next.value)}
    >
      {mode === "light" ? <IconMoon size={15} /> : <IconSun size={15} />}
    </button>
  );
}

/** 管理员登录态：读 /api/meta 判断当前访客是否已凭 Basic 凭据成为管理员；
 *  未登录时点击「登录」发一次写探测，401 触发浏览器原生登录框。 */
function useAdminState() {
  const [isAdmin, setIsAdmin] = useState<boolean | null>(null);
  const router = useRouter();

  useEffect(() => {
    fetch("/api/meta", { cache: "no-store" })
      .then((res) => (res.ok ? (res.json() as Promise<MetaData>) : null))
      .then((data) => setIsAdmin(Boolean(data?.is_admin)))
      .catch(() => setIsAdmin(false));
  }, []);

  async function login() {
    try {
      const res = await fetch("/api/auth/verify", { method: "POST", cache: "no-store" });
      if (res.ok) {
        setIsAdmin(true);
        router.push("/admin");
      }
    } catch {
      // 浏览器登录框被取消时可能抛错；保持未登录态
    }
  }

  return { isAdmin, login };
}

/** 透明浮动导航（Artificial Analysis 式）：logo 胶囊居左，菜单与动作靠右；
 *  「管理面板」入口仅在管理员登录态（或本机全开放模式）显示。 */
export function SiteNav() {
  const pathname = usePathname();
  const selected = `/${pathname.split("/")[1] ?? ""}`;
  const { mode, setMode } = useTheme();
  const { isAdmin, login } = useAdminState();
  const router = useRouter();
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
                {isAdmin && (
                  <Link
                    href="/admin"
                    onClick={() => setMenuOpen(false)}
                    className={`nav-pop-item${selected === "/admin" ? " active" : ""}`}
                  >
                    管理面板
                    {selected === "/admin" && <IconCheck size={13} />}
                  </Link>
                )}
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
          <ThemeToggle />
          {isAdmin === null ? null : isAdmin ? (
            <Btn variant="text" onClick={() => router.push("/admin")} title="已以管理员身份登录">
              管理面板
            </Btn>
          ) : (
            <Btn variant="text" onClick={login} title="输入管理员密码后可配置站点与触发采集">
              登录
            </Btn>
          )}
        </div>
      </div>
    </header>
  );
}
