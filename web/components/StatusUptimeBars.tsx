"use client";

/** 时段可用率色块条：渲染服务端预计算好的分桶（~28 段），每段颜色 = 该时段平均
 *  正常率的档位（与可用率趋势图主色一致，时段内的一次瞬间抖动不会把整块染红），
 *  悬停浮出时段范围、平均/最差值和异常渠道名单。分桶在 lib 的 buildUptimeBuckets
 *  里完成，本组件只管画和悬停。 */

import { useState } from "react";
import { formatTime } from "@/lib/format";
import { rateLevel, downNamesLabel, type UptimeBucket } from "@/lib/channelStatus";
import { useChartTheme } from "./chartTheme";

export function StatusUptimeBars({ buckets }: { buckets: (UptimeBucket | null)[] }) {
  const [hover, setHover] = useState<{ index: number; left: number } | null>(null);
  const { statusColors } = useChartTheme();
  if (buckets.length < 2) return null;
  const hoveredBucket = hover != null ? buckets[hover.index] : null;

  return (
    <div className="uptime-wrap" onMouseLeave={() => setHover(null)}>
      <div className="uptime-bars">
        {buckets.map((bucket, index) => {
          if (!bucket) return <span key={index} className="uptime-slot empty" aria-hidden />;
          const level = rateLevel(bucket.avg);
          const label = `平均正常率 ${bucket.avg}% · 最差 ${bucket.worst}%`;
          return (
            <button
              key={index}
              type="button"
              className="uptime-slot"
              style={{ background: statusColors[level] }}
              aria-label={label}
              onMouseEnter={(event) => {
                const el = event.currentTarget;
                const parent = el.offsetParent as HTMLElement | null;
                const raw = el.offsetLeft + el.offsetWidth / 2;
                const left = parent ? Math.min(Math.max(raw, 108), Math.max(parent.clientWidth - 108, 108)) : raw;
                setHover({ index, left });
              }}
              onFocus={(event) => {
                const el = event.currentTarget;
                const parent = el.offsetParent as HTMLElement | null;
                const raw = el.offsetLeft + el.offsetWidth / 2;
                const left = parent ? Math.min(Math.max(raw, 108), Math.max(parent.clientWidth - 108, 108)) : raw;
                setHover({ index, left });
              }}
              onBlur={() => setHover(null)}
            />
          );
        })}
      </div>
      {hoveredBucket && (
        <div className="uptime-tip" style={{ left: hover?.left }} aria-hidden>
          <div className="uptime-tip-title mono">
            {hoveredBucket.count > 1
              ? `${formatTime(hoveredBucket.start)} → ${formatTime(hoveredBucket.end)}`
              : formatTime(hoveredBucket.start)}
          </div>
          <div className="uptime-tip-line">
            {hoveredBucket.count} 次检测 · 平均正常{" "}
            <span style={{ color: statusColors[rateLevel(hoveredBucket.avg)] }}>{hoveredBucket.avg}%</span>
          </div>
          {hoveredBucket.worst >= 100 ? (
            <div className="uptime-tip-line" style={{ color: statusColors.ok }}>
              时段内全部正常
            </div>
          ) : (
            <div className="uptime-tip-line" style={{ color: statusColors[rateLevel(hoveredBucket.worst)] }}>
              最差 {hoveredBucket.worst}%
            </div>
          )}
          {hoveredBucket.down.length > 0 && (
            <div className="uptime-tip-line" style={{ color: statusColors.down }}>
              异常渠道：{downNamesLabel(hoveredBucket.down)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
