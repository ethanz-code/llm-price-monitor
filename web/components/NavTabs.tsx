"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { nav } from "@/lib/copy";

interface NavItem {
  key: string;
  label: string;
}

/** 大弹窗内容（copy.ts nav.popovers）：一句话说明 + 核心看点；没有配置的 tab 不出弹窗 */

/** 桌面主导航 tabs：滑动选中指示器 + hover 大弹窗（一句话说明 + 核心看点 + 进入链接）。
 *  指示器在路由切换时丝滑滑到新位置；弹窗 hover 打开、移出淡出，触屏不受影响（点击直达）。 */
export function NavTabs({ items, selected }: { items: NavItem[]; selected: string }) {
  const pathname = usePathname();
  const pillRef = useRef<HTMLElement>(null);
  const [indicator, setIndicator] = useState<{ left: number; width: number } | null>(null);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [leaving, setLeaving] = useState(false);
  const closeTimer = useRef(0);

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

  /** hover 打开弹窗；移出时先播淡出再卸载，快速滑过多个 tab 直接切换不闪 */
  const openPop = useCallback((key: string) => {
    if (!nav.popovers?.[key]) return;
    window.clearTimeout(closeTimer.current);
    setLeaving(false);
    setOpenKey(key);
  }, []);

  const closePop = useCallback(() => {
    if (!openKey) return;
    window.clearTimeout(closeTimer.current);
    setLeaving(true);
    closeTimer.current = window.setTimeout(() => {
      setOpenKey(null);
      setLeaving(false);
    }, 160);
  }, [openKey]);

  useEffect(() => () => window.clearTimeout(closeTimer.current), []);

  return (
    <nav
      className="nav-pill"
      aria-label="主导航"
      ref={pillRef}
      onMouseLeave={closePop}
    >
      {indicator && (
        <span
          aria-hidden
          className="nav-pill-indicator"
          style={{ transform: `translateX(${indicator.left}px)`, width: indicator.width }}
        />
      )}
      {items.map((item) => {
        const pop = nav.popovers?.[item.key];
        const showPop = openKey === item.key && !!pop;
        return (
          <div
            key={item.key}
            className="nav-tab-wrap"
            onMouseEnter={() => (pop ? openPop(item.key) : closePop())}
          >
            <Link
              href={item.key}
              className={`nav-pill-item${selected === item.key ? " active" : ""}`}
            >
              {item.label}
            </Link>
            {showPop && (
              <div
                className={`nav-dd${leaving ? " leaving" : ""}`}
                onMouseEnter={() => openPop(item.key)}
              >
                <div className="nav-dd-card">
                  {/* Stripe 式栏目行：主链接（大标题 + 一句话）与核心看点两栏，竖发丝线分隔 */}
                  <div className="nav-dd-main">
                    <Link
                      href={item.key}
                      className="nav-dd-entry"
                      onClick={() => setOpenKey(null)}
                    >
                      <span className="nav-dd-entry-title">{item.label}</span>
                      <span className="nav-dd-entry-desc">{pop.desc}</span>
                    </Link>
                    <Link
                      href={item.key}
                      className="nav-dd-go"
                      onClick={() => setOpenKey(null)}
                    >
                      进入{item.label} →
                    </Link>
                  </div>
                  <div className="nav-dd-side">
                    <span className="nav-dd-side-title">核心看点</span>
                    <ul className="nav-dd-points">
                      {pop.points.map((point) => (
                        <li key={point}>{point}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </nav>
  );
}
