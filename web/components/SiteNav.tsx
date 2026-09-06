"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { Btn, Modal } from "./ui";
import { IconCheck, IconClose, IconMenu, IconMoon, IconSun } from "./icons";
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

/** 未上线功能的体面占位：按钮 + "即将上线"说明弹窗；说明支持内嵌链接。 */
function ComingSoon({ label, title, description }: { label: string; title: string; description: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Btn variant="text" onClick={() => setOpen(true)}>
        {label}
      </Btn>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={title}
        footer={
          <Btn variant="primary" onClick={() => setOpen(false)}>
            知道了
          </Btn>
        }
      >
        <p style={{ color: "var(--text-2)", margin: 0, fontSize: 13.5, lineHeight: 1.7 }}>{description}</p>
      </Modal>
    </>
  );
}

/** 透明浮动导航（Artificial Analysis 式）：logo 胶囊居左，菜单与动作靠右；
 *  采集入口属于登录后的管理面板，不在公开导航露出。 */
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
          <ThemeToggle />
          <ComingSoon
            label="登录"
            title="账号体系即将上线"
            description={
              <>
                登录与多用户支持还在开发中，上线后可以管理自己的监控清单与提醒，采集任务也会收进管理面板。当前无需登录即可查看全部数据，也可以先
                <Link href="/admin">预览管理面板</Link>。
              </>
            }
          />
        </div>
      </div>
    </header>
  );
}
