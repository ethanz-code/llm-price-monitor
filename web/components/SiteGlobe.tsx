"use client";

/** 监控地球：基于 cobe（点阵 WebGL 地球，~5KB），站点按真实 IP 归属地经纬度落点。
 *  球面点带 DOM 标签（cobe CSS anchor 绑定，转到背面自动淡出），悬停可点进检测档案；
 *  清单悬停选中站点时，球把该站点转到正面中心；也可以直接按住球面拖拽转动。
 *  坐标完全相同的站点（CDN 边缘等）会围绕原点位环形散开，避免节点叠在一起；
 *  定位数据到达后节点带错峰弹入动画。定位数据由 /api/geo 提供。 */

import { Fragment, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useTheme } from "@/app/providers";
import { rateLevel } from "@/lib/channelStatus";
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

interface MarkerDef {
  location: [number, number];
  size: number;
  color: [number, number, number];
  id: string;
}

const DEG = Math.PI / 180;
const MARKER_ID = (siteId: string) => `m-${siteId}`;
/** 默认视线倾角：对齐 cobe 官方 demo 的 theta 0.2，微微俯视北半球 */
const REST_THETA = 0.2;
/** 常态自转速度（弧度/帧） */
const REST_SPEED = 0.0016;
/** 弹入动画：单个节点的时长与相邻节点的错峰间隔（ms） */
const POP_MS = 480;
const POP_STAGGER_MS = 90;

/** 节点状态色：与站点清单的分档一致（绿=优秀 ≥80%、黄=60–80%、红=<60%、灰=无检测数据/停用）。 */
function statusHex(site: GlobeSite, dark: boolean): string {
  if (site.availability == null || !site.enabled) return dark ? "#9DA3A6" : "#ADACA8";
  const level = rateLevel(site.availability);
  if (level === "warn") return dark ? "#E0B45C" : "#B45309";
  if (level === "down") return dark ? "#E27B78" : "#DC2626";
  return dark ? "#C8FF00" : "#86C200";
}

/** marker 十六进制色 → cobe 需要的 0–1 RGB。 */
function rgb01(hex: string): [number, number, number] {
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255) as [number, number, number];
}

/** 坐标几乎相同的节点（CDN 边缘节点常解析到同一点）围绕原位环形散开。 */
function spreadDuplicates(markers: MarkerDef[]): MarkerDef[] {
  const groups = new Map<string, number[]>();
  markers.forEach((marker, index) => {
    const key = `${marker.location[0].toFixed(1)}|${marker.location[1].toFixed(1)}`;
    const list = groups.get(key);
    if (list) list.push(index);
    else groups.set(key, [index]);
  });
  for (const indexes of groups.values()) {
    if (indexes.length < 2) continue;
    const radius = Math.min(6, 1.4 + 1.1 * (indexes.length - 1));
    indexes.forEach((index, k) => {
      const angle = (k / indexes.length) * Math.PI * 2 + 0.6;
      const [lat, lon] = markers[index].location;
      markers[index] = {
        ...markers[index],
        location: [
          Math.max(-80, Math.min(80, lat + radius * Math.cos(angle))),
          lon + (radius * Math.sin(angle)) / Math.max(0.3, Math.cos(lat * DEG)),
        ],
      };
    });
  }
  return markers;
}

/** 由站点清单 + 定位数据构建节点：颜色随状态分档，坐标去重散开，size 先置 0 由弹入动画抬起来。 */
function buildMarkers(sites: GlobeSite[], geo: Record<string, SiteGeo>, dark: boolean): MarkerDef[] {
  const markers = sites.flatMap((site) => {
    const loc = geo[site.id];
    if (!loc) return [];
    return [
      {
        location: [loc.lat, loc.lon] as [number, number],
        size: site.enabled ? 0.05 : 0.035,
        color: rgb01(statusHex(site, dark)),
        id: MARKER_ID(site.id),
      },
    ];
  });
  return spreadDuplicates(markers);
}

/** easeOutCubic：节点弹入用。 */
function popEase(t: number): number {
  return 1 - Math.pow(1 - Math.min(Math.max(t, 0), 1), 3);
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
  const globeRef = useRef<ReturnType<typeof createGlobe> | null>(null);
  const markersRef = useRef<MarkerDef[]>([]);
  /** 节点弹入动画的起始时刻；0 表示没有动画在进行 */
  const popStartRef = useRef(0);

  useEffect(() => {
    activeRef.current = activeId;
  }, [activeId]);
  useEffect(() => {
    hoverReportRef.current = onHoverSite;
  }, [onHoverSite]);
  useEffect(() => {
    geoRef.current = geo;
  }, [geo]);

  // 球体实例只在挂载/主题切换时重建；站点与定位变化只 update 节点，避免整球闪一下
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
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
      markers: [],
    });
    globeRef.current = globe;
    // 入场淡入改由首帧驱动：WebGL 首帧（建上下文+编译着色器）要滞后几百毫秒，
    // 之前 CSS 定时动画会在这段空白期就淡入完成，露出一块白底；现在首帧画完才加 is-ready
    canvas.classList.remove("is-ready");

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

      // 节点错峰弹入：动画期间每帧按缓动放大 size，结束后恢复整组节点
      const state: { phi: number; theta: number; markers?: MarkerDef[] } = { phi, theta };
      const start = popStartRef.current;
      if (start > 0) {
        const elapsed = performance.now() - start;
        const last = markersRef.current.length * POP_STAGGER_MS + POP_MS;
        if (elapsed >= last) {
          popStartRef.current = 0;
        } else {
          state.markers = markersRef.current.map((marker, index) => ({
            ...marker,
            size: marker.size * popEase((elapsed - index * POP_STAGGER_MS) / POP_MS),
          }));
        }
      }
      globe.update(state);
      canvas.classList.add("is-ready");

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
      globeRef.current = null;
    };
  }, [dark]);

  // 站点或定位数据变化：只换节点并触发弹入动画，不重建球体
  useEffect(() => {
    markersRef.current = buildMarkers(sites, geo, dark);
    if (markersRef.current.length === 0) return;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reducedMotion) {
      popStartRef.current = 0;
      globeRef.current?.update({ markers: markersRef.current });
      return;
    }
    popStartRef.current = performance.now();
    globeRef.current?.update({ markers: markersRef.current.map((marker) => ({ ...marker, size: 0 })) });
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
        const level = site.availability != null ? rateLevel(site.availability) : null;
        // 波纹色用主题变量而不是字面色值：字面色会随 dark 在 SSR 与客户端各算一套，触发水合不一致告警
        const pulseColorVar =
          level === "warn"
            ? "var(--chart-warn)"
            : level === "down"
              ? "var(--chart-down)"
              : "var(--chart-ok)";
        return (
          <Fragment key={site.id}>
            {/* 波纹环：钉在节点位置向外扩散，状态色随分档；无数据/停用的灰色站点不扩散 */}
            {level != null && site.enabled && (
              <span
                aria-hidden
                className="cobe-pulse"
                style={
                  {
                    positionAnchor: anchor,
                    opacity: `var(--cobe-visible-${MARKER_ID(site.id)}, 0)`,
                    "--pulse-color": pulseColorVar,
                  } as React.CSSProperties
                }
              >
                <i />
                <i />
              </span>
            )}
            <button
              type="button"
              className={`cobe-label mono${level === "down" ? " down" : level === "warn" ? " warn" : ""}`}
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
          </Fragment>
        );
      })}
    </div>
  );
}
