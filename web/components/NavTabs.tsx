"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { createPortal } from "react-dom";
import { nav } from "@/lib/copy";

/** 桌面主导航 tabs：滑动选中指示器 + 全宽 mega 面板。
 *  面板展开一次常驻，hover 在 tabs 间移动只做内容的左右滑动切换（不反复弹出收起）；
 *  条目全部是真实页面/锚点直达。移出导航区 180ms 宽限后淡出，
 *  滚动 / 路由变化 / Escape / resize 立即收起。触屏不受影响（点击直达）。 */
export function NavTabs({
  items,
  selected,
}: {
  items: { key: string; label: string }[];
  selected: string;
}) {
  const pathname = usePathname();
  const pillRef = useRef<HTMLElement>(null);
  const [indicator, setIndicator] = useState<{ left: number; width: number } | null>(null);
  const [mounted, setMounted] = useState(false);
  const [open, setOpen] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [dir, setDir] = useState<"left" | "right">("right");
  const [panelTop, setPanelTop] = useState(76);
  const closeTimer = useRef(0);
  const prevKeyRef = useRef<string | null>(null);

  useEffect(() => setMounted(true), []);

  // 滑动指示器：测量 active 项在胶囊内的位置，路由切换时滑过去
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

  /** 取消收起并恢复面板可见（离开态的淡出动画立即中断） */
  const cancelClose = useCallback(() => {
    window.clearTimeout(closeTimer.current);
    setLeaving(false);
  }, []);

  const closeNow = useCallback(() => {
    window.clearTimeout(closeTimer.current);
    setOpen(false);
    setLeaving(false);
  }, []);

  /** 移出导航区：180ms 宽限（穿过 tab 与面板之间的间隙不丢），超时淡出 */
  const scheduleClose = useCallback(() => {
    window.clearTimeout(closeTimer.current);
    setLeaving(true);
    closeTimer.current = window.setTimeout(() => {
      setOpen(false);
      setLeaving(false);
    }, 180);
  }, []);

  /** hover 到某个 tab：面板保持展开，内容带方向地滑切到该 tab */
  const enterTab = useCallback(
    (key: string) => {
      window.clearTimeout(closeTimer.current);
      setLeaving(false);
      const prev = prevKeyRef.current ?? selected;
      if (prev !== key) {
        const indexOf = (k: string) => items.findIndex((item) => item.key === k);
        const pi = indexOf(prev);
        const ni = indexOf(key);
        if (pi !== ni) setDir(ni > pi ? "right" : "left");
      }
      prevKeyRef.current = key;
      setActiveKey(key);
      const pill = pillRef.current;
      if (pill) setPanelTop(Math.round(pill.getBoundingClientRect().bottom) + 12);
      setOpen(true);
    },
    [items, selected],
  );

  // 滚动 / Escape / resize 立即收起；路由变化收起
  useEffect(() => {
    const onScroll = () => closeNow();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeNow();
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("keydown", onKey);
    };
  }, [closeNow]);

  useEffect(() => {
    closeNow();
  }, [pathname, closeNow]);

  useEffect(() => {
    const onResize = () => closeNow();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      window.clearTimeout(closeTimer.current);
    };
  }, [closeNow]);

  const activeItem = items.find((item) => item.key === activeKey) ?? null;
  const activePop = activeKey ? (nav.popovers?.[activeKey] ?? null) : null;

  return (
    <>
      <nav className="nav-pill" aria-label="主导航" ref={pillRef} onMouseLeave={scheduleClose}>
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
            onMouseEnter={() => enterTab(item.key)}
            onClick={closeNow}
          >
            {item.label}
          </Link>
        ))}
      </nav>
      {mounted && (open || leaving) && activeItem && activePop && (
        createPortal(
          <div
            className={`nav-mega${leaving ? " leaving" : ""}`}
            style={{ top: panelTop }}
            onMouseEnter={cancelClose}
            onMouseLeave={scheduleClose}
          >
            <div className="nav-mega-card">
              {/* 内容随 hover 的 tab 左右滑动切换：方向由 tab 位置决定，面板本体不动 */}
              <div className={`nav-mega-body dir-${dir}`} key={activeKey}>
                {activePop.entries.map((entry) => (
                  <Link
                    key={entry.href + entry.title}
                    href={entry.href}
                    className="nav-mega-entry"
                    onClick={closeNow}
                  >
                    <span className="nav-mega-entry-title">{entry.title}</span>
                    <span className="nav-mega-entry-desc">{entry.desc}</span>
                  </Link>
                ))}
              </div>
            </div>
          </div>,
          document.body,
        )
      )}
    </>
  );
}
