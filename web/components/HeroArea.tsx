"use client";

/** 首屏星空 hero：等距圆柱投影 2D 点阵地图全出血铺在首屏上半部做主视觉，hero 文案叠加其上
 *  （cobe 3D 球已退役，桌面与手机同一张图，断点处只换布局不改数据）；
 *  站点按 IP 归属地落点（geo 由服务端页取好传入，浏览器不再直接调数据接口）；
 *  节点画成状态色亮星，悬停地图亮星或轮播胶囊两处互相点亮，点击进检测档案；
 *  两排恒滚动：半条轨道不足一屏宽时自动复制节点补满，实现无缝循环，悬停暂停。 */

import { useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { rateLevel } from "@/lib/channelStatus";
import { SiteMapFlat, type GlobeSite, type SiteGeo } from "./SiteMapFlat";

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
  side,
}: {
  sites: GlobeSite[];
  geo: Record<string, SiteGeo>;
  main: ReactNode;
  /** AA 式 LAUNCH 侧栏位：右侧最新动态公告列，不传则保持双列 */
  side?: ReactNode;
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
            {site.availability != null ? `${site.availability}%` : "待检测"}
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
      {/* 星空层：地图与文案同格叠放（桌面文案浮在星图上，窄屏退化为上下堆叠） */}
      <div className="hero-sky">
        <SiteMapFlat
          sites={sites}
          geo={geo}
          activeId={activeId}
          onHoverSite={setActiveId}
        />
        <div className="hero-main">{main}</div>
        {side ? <aside className="hero-side">{side}</aside> : null}
      </div>
      {rows.map((row, rowIndex) => {
        // 两排都恒滚动；半条轨道不足一屏宽（按 8 个胶囊估算）时复制节点补满，位移 -50% 才无缝
        const copies = Math.max(1, Math.ceil(8 / Math.max(row.items.length, 1)));
        const half = Array.from({ length: copies }, () => row.items).flat();
        // 速度取慢档：常驻滚动太快会持续拉扯视线，慢速滚动只作氛围
        return (
          <div
            key={rowIndex}
            className={`marquee-row is-scroll${row.reverse ? " is-reverse" : ""}`}
          >
            <ul
              className="marquee-track"
              style={
                {
                  "--marquee-dur": `${Math.max(48, half.length * 6)}s`,
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
