"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";

interface NavItem {
  key: string;
  label: string;
}

/** 桌面主导航 tabs：滑动选中指示器，路由切换时白底 pill 丝滑滑到新位置。
 *  触屏与移动端由 SiteNav 的汉堡菜单承接。 */
export function NavTabs({
  items,
  selected,
}: {
  items: NavItem[];
  selected: string;
}) {
  const pathname = usePathname();
  const pillRef = useRef<HTMLElement>(null);
  const [indicator, setIndicator] = useState<{ left: number; width: number } | null>(null);

  useEffect(() => {
    const pill = pillRef.current;
    if (!pill) return;
    const measure = () => {
      const active = pill.querySelector<HTMLAnchorElement>(".nav-pill-item.active");
      if (!active) {
        setIndicator(null);
        return;
      }
      const pillRect = pill.getBoundingClientRect();
      const activeRect = active.getBoundingClientRect();
      setIndicator({ left: activeRect.left - pillRect.left, width: activeRect.width });
    };
    measure();
    // 字体加载完成会改变字宽，延迟重测一次兜底
    const timer = setTimeout(measure, 350);
    window.addEventListener("resize", measure);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("resize", measure);
    };
  }, [pathname, items.length]);

  return (
    <nav className="nav-pill" aria-label="主导航" ref={pillRef}>
      {indicator && (
        <span
          aria-hidden
          className="nav-pill-indicator"
          style={{ transform: `translateX(${indicator.left}px)`, width: indicator.width }}
        />
      )}
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
  );
}
