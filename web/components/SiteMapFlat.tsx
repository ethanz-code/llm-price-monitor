"use client";

/** hero 平面点阵地图（等距圆柱投影）：底图数据来自 lib/flatmap-dots.json（scripts/gen-flatmap.mjs
 *  从 world-atlas 110m 离线采样，运行时零请求）；站点按 IP 归属地经纬度落点。
 *  桌面与手机同一张图（HeroArea 只在断点处换布局）：PC 全出血铺在首屏上半部做星空主视觉，
 *  窄屏收窄一屏放下。节点画成状态色亮星——亮核 + 光晕 + 四芒星闪，错峰弹入、缓慢闪烁；
 *  悬停亮星浮现站名标签，并与站点轮播胶囊双向联动高亮；点击进检测档案。 */

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useTheme } from "@/app/providers";
import { rateLevel } from "@/lib/channelStatus";
import dotsData from "@/lib/flatmap-dots.json";

export interface GlobeSite {
  id: string;
  name: string;
  models: number;
  enabled: boolean;
  /** 最新时段区块平均正常率（0–100，与详情页时段色块同口径）；未接入渠道检测为 null */
  availability: number | null;
  down: number;
  checks: number;
}

export interface SiteGeo {
  ip: string;
  lat: number;
  lon: number;
  country: string;
  city: string;
}

/** 节点状态色：与站点清单的分档一致（绿=优秀 ≥80%、黄=60–80%、红=<60%、灰=无检测数据/停用），
 *  星点、波纹、轮播胶囊共用同一套分档，保证地图与清单同色同义。 */
export function statusHex(site: GlobeSite, dark: boolean): string {
  if (site.availability == null || !site.enabled) return dark ? "#9DA3A6" : "#ADACA8";
  const level = rateLevel(site.availability);
  if (level === "warn") return dark ? "#E0B45C" : "#B45309";
  if (level === "down") return dark ? "#E27B78" : "#DC2626";
  return dark ? "#45D072" : "#34A853";
}

const { latMin, latMax, dots } = dotsData as { latMin: number; latMax: number; dots: number[] };

/** 经纬度 → 地图百分比坐标（与 canvas 底图同一投影，节点按钮按 % 定位）。 */
function project(lon: number, lat: number): { left: string; top: string } {
  return {
    left: `${((lon + 180) / 360) * 100}%`,
    top: `${((latMax - lat) / (latMax - latMin)) * 100}%`,
  };
}

export function SiteMapFlat({
  sites,
  geo,
  activeId = null,
  onHoverSite,
}: {
  sites: GlobeSite[];
  /** 站点 IP 归属地（site_id → 经纬度）；没有定位数据的站点不上图 */
  geo: Record<string, SiteGeo>;
  /** 外部高亮的站点（如悬停站点轮播）：对应亮星增亮并浮现站名标签 */
  activeId?: string | null;
  /** 地图亮星悬停变化时回报站点 id，供外部清单联动 */
  onHoverSite?: (id: string | null) => void;
}) {
  const { dark } = useTheme();
  const router = useRouter();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // 底图点阵是静态数据，只有尺寸/主题变化时整帧重画，无需动画循环
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const paint = () => {
      const width = canvas.offsetWidth;
      if (width === 0) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const height = (width * (latMax - latMin)) / 360;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, width, height);
      const px = width / 360;
      // 半径随图幅放大，但封顶：全贯穿大图上底图点阵保持"远景星野"的细腻，不压过亮星节点
      const radius = Math.min(2.4, Math.max(0.7, (px * dotsData.step) / 3.1));
      ctx.fillStyle = dark ? "rgba(255, 255, 255, 0.24)" : "rgba(31, 31, 31, 0.17)";
      ctx.beginPath();
      for (let i = 0; i < dots.length; i += 2) {
        const x = (dots[i] + 180) * px;
        const y = ((latMax - dots[i + 1]) / (latMax - latMin)) * height;
        ctx.moveTo(x + radius, y);
        ctx.arc(x, y, radius, 0, Math.PI * 2);
      }
      ctx.fill();
    };
    paint();
    const observer = new ResizeObserver(paint);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [dark]);

  if (sites.length === 0) {
    return <p style={{ color: "var(--text-3)", fontSize: 13 }}>还没有站点，接入后这里会亮起第一颗星。</p>;
  }

  // 坐标相同的节点（CDN 边缘）在平面图上横向错开，避免点击区叠在一起
  const seat = new Map<string, number>();
  const located = sites.map((site) => {
    const loc = geo[site.id];
    if (!loc) return null;
    const key = `${loc.lon.toFixed(1)}|${loc.lat.toFixed(1)}`;
    const index = seat.get(key) ?? 0;
    seat.set(key, index + 1);
    return { site, loc, index };
  });
  const groupSize = new Map<string, number>();
  located.forEach((item) => {
    if (!item) return;
    const key = `${item.loc.lon.toFixed(1)}|${item.loc.lat.toFixed(1)}`;
    groupSize.set(key, Math.max(groupSize.get(key) ?? 0, item.index + 1));
  });

  return (
    <div className="pano-flatmap">
      <canvas ref={canvasRef} aria-hidden />
      {located.map((item, order) => {
        if (!item) return null;
        const { site, loc, index } = item;
        const level = site.availability != null ? rateLevel(site.availability) : null;
        const nodeColor = statusHex(site, dark);
        const pos = project(loc.lon, loc.lat);
        const siblings = groupSize.get(`${loc.lon.toFixed(1)}|${loc.lat.toFixed(1)}`) ?? 1;
        // 同坐标节点横向错开 12px；单节点不加偏移
        const left =
          siblings > 1 ? `calc(${pos.left} + ${(index - (siblings - 1) / 2) * 12}px)` : pos.left;
        const pct = site.availability != null ? `${site.availability}%` : "无检测数据";
        // 弹入按落点顺序错峰（封顶避免站点多时等太久）；闪烁相位按序号错开，天上一片星不至于齐闪
        const popDelay = Math.min(order * 65, 1100);
        const twinkleDelay = `${(order % 6) * 0.55}s`;
        return (
          <button
            key={site.id}
            type="button"
            className={`flat-node${activeId === site.id ? " on" : ""}`}
            style={
              {
                left,
                top: pos.top,
                "--node-color": nodeColor,
                "--pop-delay": `${popDelay}ms`,
                "--twinkle-delay": twinkleDelay,
              } as React.CSSProperties
            }
            aria-label={`${site.name}，${pct}，查看检测档案`}
            title={`${site.name} · ${pct}`}
            onMouseEnter={() => onHoverSite?.(site.id)}
            onMouseLeave={() => onHoverSite?.(null)}
            onFocus={() => onHoverSite?.(site.id)}
            onBlur={() => onHoverSite?.(null)}
            onClick={() => router.push(`/overview/status/${encodeURIComponent(site.id)}`)}
          >
            {level != null && site.enabled && (
              <span aria-hidden className="flat-pulse" style={{ "--pulse-color": nodeColor } as React.CSSProperties}>
                <i />
                <i />
              </span>
            )}
            <span aria-hidden className="flat-node-dot" />
            <span aria-hidden className={`flat-node-label${level === "down" ? " down" : level === "warn" ? " warn" : ""}`}>
              {site.name}
            </span>
          </button>
        );
      })}
    </div>
  );
}
