"use client";

/** 首屏监控区：左侧 cobe 大球 + 右侧文案列 + 底部跨整行的站点节点轮播；
 *  站点按 IP 归属地落点（geo 由服务端页取好传入，浏览器不再直接调数据接口）；
 *  悬停轮播里的站点，球会转过去把它送到面前高亮，球上标签也会反向点亮轮播项；
 *  两排恒滚动：半条轨道不足一屏宽时自动复制节点补满，实现无缝循环，悬停暂停。 */

import { useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { rateLevel } from "@/lib/channelStatus";
import { SiteGlobe, type GlobeSite, type SiteGeo } from "./SiteGlobe";

const TONE_TEXT: Record<"ok" | "warn" | "down", string> = {
  ok: "var(--tone-green-text)",
  warn: "var(--tone-yellow-text)",
  down: "var(--tone-red-text)",
};

/** 排序权重：异常的站浮到最前面，健康的沉底，未知/停用垫底。 */
function sortWeight(site: GlobeSite): number {
  if (site.availability != null) {
    const level = rateLevel(site.availability);
    return level === "down" ? 0 : level === "warn" ? 1 : 3;
  }
  return site.enabled ? 2 : 4;
}

export function HeroArea({
  sites,
  geo,
  main,
}: {
  sites: GlobeSite[];
  geo: Record<string, SiteGeo>;
  main: ReactNode;
}) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const router = useRouter();

  const sorted = [...sites].sort((a, b) => sortWeight(a) - sortWeight(b));

  const chip = (site: GlobeSite, row: number, ghost = false, copy = 0) => {
    const tone =
      site.availability != null ? rateLevel(site.availability) : null;
    return (
      <li
        key={`${site.id}:${row}:${copy}:${ghost ? "g" : "s"}`}
        aria-hidden={ghost || undefined}
      >
        <button
          type="button"
          tabIndex={ghost ? -1 : undefined}
          className={`pano-site${activeId === site.id ? " on" : ""}`}
          onMouseEnter={() => setActiveId(site.id)}
          onMouseLeave={() => setActiveId(null)}
          onFocus={() => setActiveId(site.id)}
          onBlur={() => setActiveId(null)}
          onClick={() =>
            router.push(`/overview/status/${encodeURIComponent(site.id)}`)
          }
        >
          <span
            aria-hidden
            className="pano-site-dot"
            style={{ background: tone ? TONE_TEXT[tone] : "var(--text-3)" }}
          />
          <span className="pano-site-name">{site.name}</span>
          <span
            className="pano-site-pct mono"
            style={tone ? { color: TONE_TEXT[tone] } : undefined}
          >
            {site.availability != null ? `${site.availability}%` : "—"}
          </span>
        </button>
      </li>
    );
  };

  // 两排节点：上排正序、下排倒序；节点多时两排反向无缝滚动
  const rows = [
    { items: sorted.slice(0, Math.ceil(sorted.length / 2)), reverse: false },
    { items: sorted.slice(Math.ceil(sorted.length / 2)), reverse: true },
  ].filter((row) => row.items.length > 0);

  return (
    <section className="hero">
      <aside className="hero-globe">
        <div className="pano-globe-layer">
          <SiteGlobe
            sites={sites}
            geo={geo}
            activeId={activeId}
            onHoverSite={setActiveId}
          />
        </div>
      </aside>
      <div className="hero-main">{main}</div>
      {rows.map((row, rowIndex) => {
        // 两排都恒滚动；半条轨道不足一屏宽（按 8 个胶囊估算）时复制节点补满，位移 -50% 才无缝
        const copies = Math.max(1, Math.ceil(8 / Math.max(row.items.length, 1)));
        const half = Array.from({ length: copies }, () => row.items).flat();
        return (
          <div
            key={rowIndex}
            className={`marquee-row is-scroll${row.reverse ? " is-reverse" : ""}`}
          >
            <ul
              className="marquee-track"
              style={
                {
                  "--marquee-dur": `${Math.max(24, half.length * 3.2)}s`,
                } as React.CSSProperties
              }
            >
              {half.map((site, index) => chip(site, rowIndex, false, Math.floor(index / row.items.length)))}
              {half.map((site, index) => chip(site, rowIndex, true, Math.floor(index / row.items.length)))}
            </ul>
          </div>
        );
      })}
    </section>
  );
}
