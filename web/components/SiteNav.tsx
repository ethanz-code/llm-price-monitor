"use client";

import { Button, Dropdown, Menu, Tooltip } from "antd";
import { MenuOutlined, MoonFilled, SunFilled } from "@ant-design/icons";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { CollectButton } from "./CollectButton";
import { ComingSoon } from "./ComingSoon";
import { LogoMark } from "./LogoMark";
import { useTheme } from "@/app/providers";
import type { ThemeMode } from "@/theme";

const ITEMS = [
  { key: "/", label: <Link href="/">首页</Link> },
  { key: "/overview", label: <Link href="/overview">价格总览</Link> },
  { key: "/history", label: <Link href="/history">历史与事件</Link> },
  { key: "/official", label: <Link href="/official">官方价库</Link> },
  { key: "/discount", label: <Link href="/discount">折扣对比</Link> },
];

const MODE_OPTIONS: { value: ThemeMode; label: string; icon: React.ReactNode }[] = [
  { value: "light", label: "浅色", icon: <SunFilled /> },
  { value: "dark", label: "深色", icon: <MoonFilled /> },
  { value: "system", label: "跟随系统", icon: <span className="mono" style={{ fontSize: 11 }}>SYS</span> },
];

function ThemeToggle() {
  const { mode, setMode } = useTheme();
  const next = MODE_OPTIONS[(MODE_OPTIONS.findIndex((o) => o.value === mode) + 1) % MODE_OPTIONS.length];
  return (
    <Tooltip title={`主题：${MODE_OPTIONS.find((o) => o.value === mode)?.label}，切换到${next.label}`}>
      <button aria-label="切换主题" className="icon-btn" onClick={() => setMode(next.value)}>
        {mode === "light" ? <MoonFilled /> : <SunFilled />}
      </button>
    </Tooltip>
  );
}

export function SiteNav() {
  const pathname = usePathname();
  const selected = `/${pathname.split("/")[1] ?? ""}`;
  return (
    <header
      style={{
        background: "var(--header-bg)",
        backdropFilter: "blur(14px)",
        WebkitBackdropFilter: "blur(14px)",
        borderBottom: "1px solid var(--border)",
        position: "sticky",
        top: 0,
        zIndex: 100,
      }}
    >
      <div className="nav-inner">
        <Link href="/" style={{ display: "flex", alignItems: "center", gap: 10, textDecoration: "none" }}>
          <LogoMark size={22} />
          <span className="brand-name" style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.02em", color: "var(--text)" }}>
            LLM 价格监控
          </span>
          <span className="mono brand-version" style={{ fontSize: 11, color: "var(--text-3)", marginTop: 2 }}>
            v0.1
          </span>
        </Link>
        <Menu
          mode="horizontal"
          selectedKeys={[selected === "/" ? "/" : selected]}
          items={ITEMS}
          className="nav-menu"
          style={{ flex: 1, borderBottom: "none", background: "transparent", minWidth: 0 }}
        />
        <Dropdown
          menu={{ items: ITEMS.map((item) => ({ key: item.key, label: item.label })), selectable: true, selectedKeys: [selected] }}
          trigger={["click"]}
          placement="bottomLeft"
        >
          <Button className="nav-mobile-menu" icon={<MenuOutlined />} aria-label="打开导航菜单" />
        </Dropdown>
        <div className="nav-actions">
          <ComingSoon
            label="登录"
            type="text"
            title="账号体系即将上线"
            description="登录与多用户支持还在开发中，上线后可以管理自己的监控清单与提醒。当前无需登录即可查看全部数据。"
          />
          <ThemeToggle />
          <CollectButton />
        </div>
      </div>
    </header>
  );
}
