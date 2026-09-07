/** 页脚右下角的溶解式镀铬马赛克：挂在 .site-footer 内，与 HeroBackdrop 同源同质感。 */
import type { CSSProperties } from "react";
import { buildChromeCells, CHROME_STOPS, CHROME_DIRS, type ChromeCell } from "@/lib/chrome";

const CHROME_CELLS: ChromeCell[] = buildChromeCells({
  cx: 460,
  cy: 300,
  rx: 255,
  ry: 160,
  cell: 26,
  seed: 19960402,
  gradientPrefix: "cm-chr",
});

export function ChromeMosaic() {
  return (
    <div className="chrome-mosaic" aria-hidden>
      <svg width="100%" height="100%" viewBox="0 0 560 340" preserveAspectRatio="xMidYMid slice">
        <defs>
          {CHROME_DIRS.map(([x2, y2], i) => (
            <linearGradient key={i} id={`cm-chr-${i}`} x1="0" y1="0" x2={x2} y2={y2}>
              {CHROME_STOPS.map(([offset, color]) => (
                <stop key={offset} offset={offset} style={{ stopColor: color }} />
              ))}
            </linearGradient>
          ))}
          <linearGradient id="cm-chr-g" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" style={{ stopColor: "var(--chrome-hi)" }} />
            <stop offset="0.45" style={{ stopColor: "var(--chrome-glint)" }} />
            <stop offset="1" style={{ stopColor: "var(--chrome-lo)" }} />
          </linearGradient>
        </defs>
        <g style={{ opacity: "var(--chrome-opacity)" }}>
          {CHROME_CELLS.map((cell, i) => (
            <rect
              key={i}
              className="chr-tile"
              x={cell.x}
              y={cell.y}
              width={cell.size}
              height={cell.size}
              fill={cell.fill}
              style={{ "--o": cell.opacity, animationDelay: `${cell.delay}s`, animationDuration: `${cell.dur}s` } as CSSProperties}
            />
          ))}
        </g>
      </svg>
    </div>
  );
}
