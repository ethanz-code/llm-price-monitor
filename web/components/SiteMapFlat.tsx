"use client";

/** 手机端 hero 平面点阵地图：cobe 3D 球在窄屏只看得见正面半球、拖转和 CSS anchor 标签
 *  都是给指针设备设计的，触屏上等于只展示一半节点。换成等距圆柱投影的 2D 点阵图，
 *  全部节点一屏尽收。底图数据来自 lib/flatmap-dots.json（scripts/gen-flatmap.mjs
 *  从 world-atlas 110m 离线采样，运行时零请求）；节点状态色、波纹、点击跳检测档案
 *  与球面版（SiteGlobe）同一套口径。 */

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useTheme } from "@/app/providers";
import { rateLevel } from "@/lib/channelStatus";
import dotsData from "@/lib/flatmap-dots.json";
import { statusHex, type GlobeSite, type SiteGeo } from "./SiteGlobe";

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
}: {
  sites: GlobeSite[];
  /** 站点 IP 归属地（site_id → 经纬度）；没有定位数据的站点不上图 */
  geo: Record<string, SiteGeo>;
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
      const radius = Math.max(0.7, (px * dotsData.step) / 3.1);
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
      {located.map((item) => {
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
        return (
          <button
            key={site.id}
            type="button"
            className="flat-node"
            style={{ left, top: pos.top, "--node-color": nodeColor } as React.CSSProperties}
            aria-label={`${site.name}，${pct}，查看检测档案`}
            title={`${site.name} · ${pct}`}
            onClick={() => router.push(`/overview/status/${encodeURIComponent(site.id)}`)}
          >
            {level != null && site.enabled && (
              <span aria-hidden className="flat-pulse" style={{ "--pulse-color": nodeColor } as React.CSSProperties}>
                <i />
                <i />
              </span>
            )}
            <span aria-hidden className="flat-node-dot" />
          </button>
        );
      })}
    </div>
  );
}
