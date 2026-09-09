"use client";

/** 访客来源世界地图：一张全球大图，按国家着色表示访问热度。
 *  地图数据来自 public/world-geo.json（world-atlas 110m TopoJSON，properties.zh 为 babel 中文国名），
 *  亮色主题为描线风格（只勾轮廓，有访问的国家用品牌色描边），暗色主题为填色风格。 */

import { useEffect, useMemo, useRef, useState } from "react";
import { geoNaturalEarth1, geoPath } from "d3-geo";
import { feature } from "topojson-client";
import { useChartTheme, ChartBubble } from "./chartTheme";

export interface RegionStat {
  name: string;
  pv: number;
  uv: number;
}

interface WorldTopology {
  objects: { countries: unknown };
}

type WorldFeature = GeoJSON.Feature<GeoJSON.Geometry, { zh?: string; name?: string }>;

/** 南极洲与无主领土不上图。 */
const EXCLUDED_IDS = new Set(["010"]);

export function VisitorMap({ regions }: { regions: RegionStat[] }) {
  const { dark, lineColor } = useChartTheme();
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [countries, setCountries] = useState<WorldFeature[] | null>(null);
  const [hover, setHover] = useState<{ name: string; pv: number; uv: number; x: number; y: number } | null>(null);

  useEffect(() => {
    fetch("/world-geo.json")
      .then((resp) => resp.json())
      .then((topo: WorldTopology) => {
        const geo = feature(topo as never, topo.objects.countries as never);
        const list = (geo as unknown as GeoJSON.FeatureCollection).features as WorldFeature[];
        setCountries(list.filter((f) => !EXCLUDED_IDS.has(String((f as { id?: string }).id ?? ""))));
      })
      .catch(() => setCountries(null));
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => {
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const byName = useMemo(() => new Map(regions.map((item) => [item.name, item])), [regions]);
  const maxPv = Math.max(1, ...regions.map((item) => item.pv));

  const paths = useMemo(() => {
    if (!countries || size.width === 0 || size.height === 0) return [];
    const projection = geoNaturalEarth1().fitExtent(
      [
        [4, 4],
        [size.width - 4, size.height - 4],
      ],
      { type: "Sphere" } as never,
    );
    const path = geoPath(projection);
    return countries.map((f) => {
      const name = f.properties?.zh ?? f.properties?.name ?? "";
      const stat = byName.get(name);
      return {
        key: String((f as { id?: string }).id ?? name),
        name,
        d: path(f) ?? "",
        pv: stat?.pv ?? 0,
        uv: stat?.uv ?? 0,
      };
    });
  }, [countries, size, byName]);

  function paint(pv: number): { fill: string; stroke: string; strokeWidth: number } {
    if (dark) {
      if (pv <= 0) return { fill: "rgba(255,255,255,0.06)", stroke: "rgba(255,255,255,0.12)", strokeWidth: 0.5 };
      const t = Math.sqrt(pv / maxPv);
      return { fill: `rgba(200, 255, 0, ${0.2 + t * 0.8})`, stroke: "rgba(0,0,0,0.4)", strokeWidth: 0.5 };
    }
    // 亮色：描线风格——底图只勾淡轮廓，有访问的国家用品牌色描边加深
    if (pv <= 0) return { fill: "transparent", stroke: "rgba(0,0,0,0.16)", strokeWidth: 0.5 };
    const t = Math.sqrt(pv / maxPv);
    return {
      fill: `rgba(134, 194, 0, ${0.08 + t * 0.22})`,
      stroke: `rgba(96, 160, 0, ${0.65 + t * 0.35})`,
      strokeWidth: 0.8 + t * 0.8,
    };
  }

  return (
    <div
      ref={containerRef}
      style={{ position: "relative", width: "100%", height: "100%" }}
      onMouseLeave={() => setHover(null)}
    >
      {paths.length > 0 && (
        <svg width={size.width} height={size.height} role="img" aria-label="访客来源世界地图">
          {paths.map((path) => {
            const style = paint(path.pv);
            return (
              <path
                key={path.key}
                d={path.d}
                fill={style.fill}
                stroke={style.stroke}
                strokeWidth={style.strokeWidth}
                onMouseMove={(event) => {
                  if (!path.name) return;
                  const rect = containerRef.current?.getBoundingClientRect();
                  setHover({
                    name: path.name,
                    pv: path.pv,
                    uv: path.uv,
                    x: event.clientX - (rect?.left ?? 0),
                    y: event.clientY - (rect?.top ?? 0),
                  });
                }}
              />
            );
          })}
        </svg>
      )}
      {hover && (
        <div
          style={{
            position: "absolute",
            left: Math.min(hover.x + 14, size.width - 150),
            top: Math.max(hover.y - 10, 0),
            pointerEvents: "none",
            zIndex: 2,
          }}
        >
          <ChartBubble
            label={hover.name}
            rows={[
              { color: lineColor, name: "访问量", value: hover.pv },
              { name: "访客数", value: hover.uv },
            ]}
          />
        </div>
      )}
    </div>
  );
}
