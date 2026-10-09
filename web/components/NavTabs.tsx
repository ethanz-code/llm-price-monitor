"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { createPortal } from "react-dom";
import { nav } from "@/lib/copy";

/** 桌面主导航 tabs：滑动选中指示器 + 全宽 mega 面板。
 *  面板铺满视口宽度（两侧留边、与 tabs 之间留间隙），hover 在不同 tabs 之间移动时
 *  面板保持展开、内容平滑切换，不反复弹出收起；移出导航区短暂宽限后淡出，
 *  滚动 / 路由变化 / Escape 立即收起。触屏不受影响（hover 不触发，点击直达）。 */
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
  const [panelTop, setPanelTop] = useState(76);
  const closeTimer = useRef(0);

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

  const cancelClose = useCallback(() => window.clearTimeout(closeTimer.current), []);

  const closeNow = useCallback(() => {
    window.clearTimeout(closeTimer.current);
    setOpen(false);
    setLeaving(false);
  }, []);

  /** 移出导航区：给 180ms 宽限（穿过 tab 与面板之间的间隙不丢），超时淡出 */
  const scheduleClose = useCallback(() => {
    window.clearTimeout(closeTimer.current);
    setLeaving(true);
    closeTimer.current = window.setTimeout(() => {
      setOpen(false);
      setLeaving(false);
    }, 180);
  }, []);

  /** hover 到某个 tab：面板保持展开，内容切到该 tab */
  const enterTab = useCallback(
    (key: string) => {
      window.clearTimeout(closeTimer.current);
      setLeaving(false);
      setActiveKey(key);
      const pill = pillRef.current;
      if (pill) setPanelTop(Math.round(pill.getBoundingClientRect().bottom) + 12);
      setOpen(true);
    },
    [],
  );

  // 滚动 / Escape 立即收起；路由变化收起；resize 收起（面板位置失效）
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
              {/* 内容随 hover 的 tab 切换：key 变化触发 swap 动画，面板本体不动 */}
              <div className="nav-mega-body" key={activeKey}>
                <div className="nav-mega-main">
                  <span className="nav-mega-label">{activeItem.label}</span>
                  <p className="nav-mega-desc">{activePop.desc}</p>
                  <Link
                    href={activeItem.key}
                    className="nav-mega-go"
                    onClick={closeNow}
                  >
                    进入{activeItem.label} →
                  </Link>
                </div>
                <div className="nav-mega-side">
                  <span className="nav-mega-side-title">核心看点</span>
                  <div className="nav-mega-points">
                    {activePop.points.map((point) => (
                      <div key={point} className="nav-mega-point">
                        <span className="nav-mega-point-dot" aria-hidden />
                        <span>{point}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>,
          document.body,
        )
      )}
    </>
  );
}
