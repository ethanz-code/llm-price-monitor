"use client";

/** 监控地球：基于 cobe（点阵 WebGL 地球，~5KB），站点按真实 IP 归属地经纬度落点。
 *  球面点带 DOM 标签（cobe CSS anchor 绑定，转到背面自动淡出），悬停可点进检测档案；
 *  清单悬停选中站点时，球把该站点转到正面中心；也可以直接按住球面拖拽转动。
 *  定位数据由 /api/geo 提供。 */

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useTheme } from "@/app/providers";
import createGlobe from "cobe";

export interface GlobeSite {
  id: string;
  name: string;
  models: number;
  enabled: boolean;
  /** 最近一次渠道正常率（0–100）；未接入渠道检测为 null */
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

const DEG = Math.PI / 180;
const MARKER_ID = (siteId: string) => `m-${siteId}`;
/** 默认视线倾角：对齐 cobe 官方 demo 的 theta 0.2，微微俯视北半球 */
const REST_THETA = 0.2;
/** 常态自转速度（弧度/帧） */
const REST_SPEED = 0.0016;

/** marker 十六进制色 → cobe 需要的 0–1 RGB。 */
function rgb01(hex: string): [number, number, number] {
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255) as [number, number, number];
}

export function SiteGlobe({
  sites,
  geo,
  activeId = null,
  onHoverSite,
}: {
  sites: GlobeSite[];
  /** 站点 IP 归属地（site_id → 经纬度）；没有定位数据的站点不上球 */
  geo: Record<string, SiteGeo>;
  /** 外部高亮的站点（如悬停站点清单）：球会转到它面前 */
  activeId?: string | null;
  /** 球上标签悬停变化时回报站点 id，供外部清单联动 */
  onHoverSite?: (id: string | null) => void;
}) {
  const { dark } = useTheme();
  const router = useRouter();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const activeRef = useRef<string | null>(activeId);
  const labelHoverRef = useRef<string | null>(null);
  const hoverReportRef = useRef(onHoverSite);
  const geoRef = useRef(geo);
  const dragRef = useRef({ active: false, x: 0, y: 0 });
  const lastInteractRef = useRef(0);

  useEffect(() => {
    activeRef.current = activeId;
  }, [activeId]);
  useEffect(() => {
    hoverReportRef.current = onHoverSite;
  }, [onHoverSite]);
  useEffect(() => {
    geoRef.current = geo;
  }, [geo]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const markers = sites.flatMap((site) => {
      const loc = geoRef.current[site.id];
      if (!loc) return [];
      return [
        {
          location: [loc.lat, loc.lon] as [number, number],
          size: site.enabled ? 0.085 : 0.055,
          color: rgb01(site.enabled ? (dark ? "#C8FF00" : "#86C200") : dark ? "#9DA3A6" : "#ADACA8"),
          id: MARKER_ID(site.id),
        },
      ];
    });

    let phi = 0.9;
    let theta = REST_THETA;
    let width = canvas.offsetWidth;
    // 入场：先快速转一圈再减速到常速，配合 CSS 淡入放大
    let spin = reducedMotion ? REST_SPEED : 0.055;

    const globe = createGlobe(canvas, {
      devicePixelRatio: Math.min(window.devicePixelRatio || 1, 2),
      width,
      height: width,
      phi,
      theta,
      dark: dark ? 1 : 0,
      // 形态对齐 cobe 官方 demo（V2）：diffuse 1.2 / mapBrightness 6 / theta 0.2，
      // 颜色沿用项目主题：品牌绿标记点，深浅两套底色
      diffuse: 1.2,
      mapSamples: 16000,
      mapBrightness: 6,
      baseColor: dark ? [0.3, 0.3, 0.32] : [1, 1, 1],
      // 标记点两种主题都用品牌绿（亮色下用深一档的 #86C200 保证对比度）
      markerColor: dark ? [0.78, 1, 0] : [0.525, 0.76, 0],
      glowColor: dark ? [0.15, 0.19, 0.06] : [1, 1, 1],
      markers,
    });

    // npm 上的 cobe 2.0.1 没有内部渲染循环（onRender 是仓库未发布代码），
    // 每帧自己算好经纬角再调 update() 驱动重绘，标签锚点也会随之更新。
    const frame = () => {
      const activeNow = activeRef.current;
      const activeLoc = activeNow ? geoRef.current[activeNow] : undefined;
      if (dragRef.current.active || labelHoverRef.current) {
        // 拖拽中或鼠标压在球面标签上：完全停住，球不能从光标底下溜走
      } else if (Date.now() - lastInteractRef.current < 2600) {
        // 刚松手：歇一小会儿再恢复自转，避免立刻从手里"逃走"
      } else if (activeLoc) {
        // 清单联动：经纬度一起转，把选中站点送到正面中心
        let target = 1.5 * Math.PI - activeLoc.lon * DEG;
        let delta = (target - phi) % (Math.PI * 2);
        if (delta > Math.PI) delta -= Math.PI * 2;
        if (delta < -Math.PI) delta += Math.PI * 2;
        phi += delta * 0.08;
        theta += (Math.max(-1.1, Math.min(1.1, activeLoc.lat * DEG)) - theta) * 0.08;
      } else {
        theta += (REST_THETA - theta) * 0.05;
        if (!reducedMotion) phi += spin;
        spin += (REST_SPEED - spin) * 0.02;
      }
      globe.update({ phi, theta });
      raf = requestAnimationFrame(frame);
    };
    let raf = requestAnimationFrame(frame);

    // 手动拖拽：按住球面左右拖转经度、上下拖转倾角
    const onPointerDown = (event: PointerEvent) => {
      if (event.button !== 0 && event.pointerType === "mouse") return;
      dragRef.current = { active: true, x: event.clientX, y: event.clientY };
      canvas.setPointerCapture(event.pointerId);
      canvas.style.cursor = "grabbing";
    };
    const onPointerMove = (event: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag.active) return;
      phi += (event.clientX - drag.x) * 0.005;
      // 竖直方向取正号：往下拖时球面跟着手往下走（与水平方向手感一致）
      theta = Math.max(-1.1, Math.min(1.1, theta + (event.clientY - drag.y) * 0.005));
      drag.x = event.clientX;
      drag.y = event.clientY;
      lastInteractRef.current = Date.now();
    };
    const onPointerUp = () => {
      if (!dragRef.current.active) return;
      dragRef.current.active = false;
      lastInteractRef.current = Date.now();
      canvas.style.cursor = "grab";
    };
    canvas.addEventListener("pointerdown", onPointerDown);
    canvas.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("pointerup", onPointerUp);
    canvas.addEventListener("pointercancel", onPointerUp);

    const observer = new ResizeObserver(() => {
      const next = canvas.offsetWidth;
      if (next > 0 && next !== width) {
        width = next;
        globe.update({ width, height: width });
      }
    });
    observer.observe(canvas);

    return () => {
      observer.disconnect();
      cancelAnimationFrame(raf);
      canvas.removeEventListener("pointerdown", onPointerDown);
      canvas.removeEventListener("pointermove", onPointerMove);
      canvas.removeEventListener("pointerup", onPointerUp);
      canvas.removeEventListener("pointercancel", onPointerUp);
      globe.destroy();
    };
  }, [sites, geo, dark]);

  if (sites.length === 0) {
    return <p style={{ color: "var(--text-3)", fontSize: 13 }}>还没有站点，接入后这里会亮起第一颗星。</p>;
  }

  const located = sites.filter((site) => geo[site.id]);

  return (
    <div className="pano-globe">
      <canvas ref={canvasRef} aria-label="监控站点地球：站点按服务器所在地落点，悬停查看，点击进入检测档案" />
      {located.map((site) => {
        const anchor = `--cobe-${MARKER_ID(site.id)}`;
        const tone = site.availability != null && site.availability < 90;
        return (
          <button
            key={site.id}
            type="button"
            className={`cobe-label mono${tone ? " warn" : ""}`}
            style={
              {
                positionAnchor: anchor,
                opacity: `var(--cobe-visible-${MARKER_ID(site.id)}, 0)`,
              } as React.CSSProperties
            }
            onMouseEnter={() => {
              labelHoverRef.current = site.id;
              hoverReportRef.current?.(site.id);
            }}
            onMouseLeave={() => {
              labelHoverRef.current = null;
              hoverReportRef.current?.(null);
            }}
            onFocus={() => {
              labelHoverRef.current = site.id;
              hoverReportRef.current?.(site.id);
            }}
            onBlur={() => {
              labelHoverRef.current = null;
              hoverReportRef.current?.(null);
            }}
            onClick={() => router.push(`/overview/status/${encodeURIComponent(site.id)}`)}
          >
            {site.name}
          </button>
        );
      })}
    </div>
  );
}
