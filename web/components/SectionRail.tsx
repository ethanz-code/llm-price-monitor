"use client";

import { useEffect, useState } from "react";

/** 首页右侧吸顶区块目录（AA 同款）：IntersectionObserver 高亮当前区块，点击锚点跳转。 */
const RAIL_ITEMS = [
  { id: "sec-latest", label: "最新价格" },
  { id: "sec-trend", label: "价格走势" },
  { id: "sec-sites", label: "检测站点" },
  { id: "sec-rankings", label: "模型榜单" },
  { id: "sec-faq", label: "常见问题" },
];

export function SectionRail() {
  const [active, setActive] = useState("");

  useEffect(() => {
    const sections = RAIL_ITEMS.map((item) => document.getElementById(item.id)).filter(
      (el): el is HTMLElement => el != null,
    );
    if (sections.length === 0) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const current = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (current) setActive(current.target.id);
      },
      // 视口上部为判定带：滚过即切到下一区块
      { rootMargin: "-15% 0px -70% 0px" },
    );
    sections.forEach((section) => observer.observe(section));
    return () => observer.disconnect();
  }, []);

  return (
    <nav className="section-rail" aria-label="页面区块">
      {RAIL_ITEMS.map((item) => (
        <a
          key={item.id}
          href={`#${item.id}`}
          className={active === item.id ? "rail-item on" : "rail-item"}
        >
          <span className="rail-label">{item.label}</span>
          <span className="rail-dot" aria-hidden />
        </a>
      ))}
    </nav>
  );
}
