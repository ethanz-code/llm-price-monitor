"use client";

/** 首屏监控地球：cobe 大球做主视觉，下方站点清单；
 *  站点按 IP 归属地落点（客户端异步拉 /api/geo，不阻塞页面）；
 *  悬停清单里的站点，球会转过去把它送到面前高亮，球上标签也会反向点亮清单项。 */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiGet } from "@/lib/api";
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

export function GlobePanel({ sites }: { sites: GlobeSite[] }) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const [geo, setGeo] = useState<Record<string, SiteGeo>>({});
  const router = useRouter();

  // 站点定位较慢（DNS + 归属地查询），客户端异步拉取：球先转起来，节点好了再亮
  useEffect(() => {
    let alive = true;
    apiGet<{ geo: Record<string, SiteGeo> }>("/api/geo")
      .then((data) => {
        if (alive) setGeo(data.geo ?? {});
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="pano">
      <div className="pano-globe-layer">
        <SiteGlobe sites={sites} geo={geo} activeId={activeId} onHoverSite={setActiveId} />
      </div>
      <ul className={`pano-sites${sites.length > 8 ? " wide" : ""}${sites.length > 14 ? " scroll" : ""}`}>
        {[...sites].sort((a, b) => sortWeight(a) - sortWeight(b)).map((site) => {
          const tone = site.availability != null ? rateLevel(site.availability) : null;
          return (
            <li key={site.id}>
              <button
                type="button"
                className={`pano-site${activeId === site.id ? " on" : ""}`}
                onMouseEnter={() => setActiveId(site.id)}
                onMouseLeave={() => setActiveId(null)}
                onFocus={() => setActiveId(site.id)}
                onBlur={() => setActiveId(null)}
                onClick={() => router.push(`/overview/status/${encodeURIComponent(site.id)}`)}
              >
                <span aria-hidden className="pano-site-dot" style={{ background: tone ? TONE_TEXT[tone] : "var(--text-3)" }} />
                <span className="pano-site-name">{site.name}</span>
                <span className="pano-site-pct mono" style={tone ? { color: TONE_TEXT[tone] } : undefined}>
                  {site.availability != null ? `${site.availability}%` : "—"}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
