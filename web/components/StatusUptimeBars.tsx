"use client";

/** 时段可用率色块条：把检测点序列自适应切成 ~28 段，每段颜色 = 该时段最差
 *  正常率的三档档位（与可用率趋势图同色），悬停浮出时段范围、平均/最差值和
 *  异常渠道名单。数据只有几分钟就是分钟块，攒到几周自动变天块。 */

import { useState } from "react";
import { formatTime } from "@/lib/format";
import { rateLevel, downNamesLabel, type AvailabilityPoint } from "@/lib/channelStatus";
import { useChartTheme } from "./chartTheme";

interface UptimeBucket {
  start: number;
  end: number;
  count: number;
  worst: number;
  sum: number;
  down: string[];
}

const TARGET_BUCKETS = 28;

function buildBuckets(points: AvailabilityPoint[]): (UptimeBucket | null)[] {
  const t0 = points[0].at;
  const t1 = points[points.length - 1].at;
  const size = Math.max((t1 - t0) / TARGET_BUCKETS, 1);
  const total = Math.min(Math.max(Math.ceil((t1 - t0) / size), 1), TARGET_BUCKETS);
  const slots: (UptimeBucket | null)[] = Array.from({ length: total }, () => null);
  for (const point of points) {
    const index = Math.min(Math.floor((point.at - t0) / size), total - 1);
    const bucket = slots[index];
    if (bucket) {
      bucket.end = point.at;
      bucket.count += 1;
      bucket.sum += point.pct;
      bucket.worst = Math.min(bucket.worst, point.pct);
      for (const name of point.down) {
        if (!bucket.down.includes(name)) bucket.down.push(name);
      }
    } else {
      slots[index] = { start: point.at, end: point.at, count: 1, worst: point.pct, sum: point.pct, down: [...point.down] };
    }
  }
  return slots;
}

export function StatusUptimeBars({ points }: { points: AvailabilityPoint[] }) {
  const [hover, setHover] = useState<{ index: number; left: number } | null>(null);
  const { statusColors } = useChartTheme();
  if (points.length < 2) return null;
  const buckets = buildBuckets(points);
  const hoveredBucket = hover != null ? buckets[hover.index] : null;
  const avgPct = hoveredBucket ? Math.round(hoveredBucket.sum / hoveredBucket.count) : 0;

  return (
    <div className="uptime-wrap" onMouseLeave={() => setHover(null)}>
      <div className="uptime-bars">
        {buckets.map((bucket, index) => {
          if (!bucket) return <span key={index} className="uptime-slot empty" aria-hidden />;
          const level = rateLevel(bucket.worst);
          const label = `平均正常率 ${Math.round(bucket.sum / bucket.count)}% · 最差 ${bucket.worst}%`;
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
            <span style={{ color: statusColors[rateLevel(avgPct)] }}>{avgPct}%</span>
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
