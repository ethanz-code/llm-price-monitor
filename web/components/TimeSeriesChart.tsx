"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ChartBubble, useChartTheme } from "./chartTheme";
import { formatTime } from "@/lib/format";
import type { RateLevel } from "@/lib/channelStatus";

/** SSR 环境退回 useEffect，客户端一律用同步 layout effect：
 *  普通effect在后台标签页会被调度器推迟，首屏画布会一直空白。 */
const useIsomorphicLayoutEffect = typeof document === "undefined" ? useEffect : useLayoutEffect;

export interface TimeSeries {
  name: string;
  /** 与 times 一一对齐的值；null 表示该时刻之前还没有检测数据。 */
  values: (number | null)[];
}

const PAD = { left: 46, right: 12, top: 10, bottom: 22 };
const MINI_HEIGHT = 34;
const EDGE_GRAB_PX = 8;

/**
 * 自绘 canvas 时序图（主图 + 概览刷选条 + 图例），替代 recharts。
 * recharts 的 Brush 每帧都会触发整棵 SVG 重渲染，几千个点时拖动必然掉帧；
 * canvas 每次拖动只重画一张位图（万级点也在 1–2ms 内），这是卡顿问题的根治方案。
 * step=true 时按阶梯线绘制（检测点之间保持上次结果），否则直线相连（缺口跨过）。
 * 时间轴窗口以「全跨度的比例」（0–1）交给父组件持有，多个图传同一个值即可联动。
 */
export function TimeSeriesChart({
  times,
  series,
  step = false,
  yDomain,
  yFormat = (v: number) => `${v}`,
  height = 220,
  levelOf,
  levelLabels,
  window: windowProp,
  onWindowChange,
}: {
  times: number[];
  series: TimeSeries[];
  step?: boolean;
  yDomain?: [number, number];
  yFormat?: (v: number) => string;
  height?: number;
  /** 分档着色（整站可用率三档用）：给每个下标返回档位，线与填充按档位换色。 */
  levelOf?: (index: number) => RateLevel;
  /** levelOf 存在时的图例文案（档位 → 说明）。 */
  levelLabels?: Record<RateLevel, string>;
  /** 时间轴窗口，取全跨度比例；null 表示全部。不传则组件内部自持。 */
  window?: [number, number] | null;
  onWindowChange?: (value: [number, number] | null) => void;
}) {
  const { dark, axisColor, gridColor, palette, statusColors } = useChartTheme();
  const wrapRef = useRef<HTMLDivElement>(null);
  const mainRef = useRef<HTMLCanvasElement>(null);
  const miniRef = useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = useState(0);
  const [innerWindow, setInnerWindow] = useState<[number, number] | null>(null);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());
  const [hover, setHover] = useState<number | null>(null);
  const drag = useRef<{ mode: "move" | "start" | "end" | "new"; anchor?: number; grab?: number; length?: number } | null>(null);
  /** 入场揭示动画进度（0–1）：只在数据变化时播放一次，拖拽/悬停不参与。 */
  const [reveal, setReveal] = useState(1);
  const rafRef = useRef<number | null>(null);
  const dataRef = useRef(times);

  useEffect(() => {
    if (dataRef.current === times) return;
    dataRef.current = times;
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    setReveal(0);
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min((now - start) / 450, 1);
      setReveal(1 - Math.pow(1 - t, 3));
      rafRef.current = t < 1 ? requestAnimationFrame(tick) : null;
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [times]);

  const windowFrac = windowProp !== undefined ? windowProp : innerWindow;
  const setWindowFrac = onWindowChange ?? setInnerWindow;

  useIsomorphicLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setWidth(el.clientWidth));
    observer.observe(el);
    setWidth(el.clientWidth);
    return () => observer.disconnect();
  }, []);

  const visible = useMemo(() => series.filter((s) => !hidden.has(s.name)), [series, hidden]);
  const colorOf = useMemo(() => series.map((_, i) => palette[i % palette.length]), [series, palette]);

  const count = times.length;
  /** 窗口最小宽度：再怎么拖也至少留 ~2% / 8 个点，拖到头不会把窗口捏没成一两个点。 */
  const minSpan = Math.min(Math.max(8 / Math.max(count - 1, 1), 0.02), 1);
  const i0 = windowFrac ? Math.max(0, Math.min(Math.round(windowFrac[0] * (count - 1)), count - 2)) : 0;
  const i1 = windowFrac ? Math.min(count - 1, Math.max(Math.round(windowFrac[1] * (count - 1)), i0 + 2)) : count - 1;
  const span = Math.max(i1 - i0, 1);

  useIsomorphicLayoutEffect(() => {
    if (width < 60 || count < 2) return;
    // 后台标签页等场景 ResizeObserver 可能漏发，绘制前实测一次容器宽度，不一致就更新重画
    const actual = wrapRef.current?.clientWidth ?? width;
    if (Math.abs(actual - width) > 1) {
      setWidth(actual);
      return;
    }
    const dpr = globalThis.devicePixelRatio || 1;
    const plotW = width - PAD.left - PAD.right;
    const plotH = height - PAD.top - PAD.bottom;
    const xOf = (i: number) => PAD.left + ((i - i0) / span) * plotW;
    const yOf = (v: number) => PAD.top + (1 - (v - y0) / (y1 - y0)) * plotH;
    const clippedYOf = (v: number) => Math.min(Math.max(yOf(v), PAD.top), PAD.top + plotH);

    // Y 轴范围：外部指定（可用率 0–100）或按可见数据分位数自适应（延迟）。
    // 用 P2/P98 而不是极值：个别自报异常点（如 58 秒）不会把其余数据压成一条直线，
    // 超出量程的尖峰由 clippedYOf 裁掉。
    let y0: number;
    let y1: number;
    if (yDomain) {
      [y0, y1] = yDomain;
    } else {
      const values: number[] = [];
      for (const s of visible) {
        for (let i = i0; i <= i1; i += 1) {
          const v = s.values[i];
          if (v != null) values.push(v);
        }
      }
      values.sort((a, b) => a - b);
      const quantile = (p: number) => values[Math.min(values.length - 1, Math.max(0, Math.floor(values.length * p)))] ?? 0;
      const lo = values.length > 0 ? quantile(0.02) : 0;
      const hi = values.length > 0 ? quantile(0.98) : 1;
      const pad = (hi - lo) * 0.12 || Math.max(Math.abs(hi) * 0.1, 1);
      y0 = lo >= 0 ? Math.max(lo - pad, 0) : lo - pad;
      y1 = hi + pad;
    }

    /** 沿阶梯/直线走到 to（含），pen 规则与整段绘制一致；供分段着色复用。 */
    const walkPath = (ctx: CanvasRenderingContext2D, s: TimeSeries, from: number, to: number) => {
      ctx.beginPath();
      let pen = false;
      let prevY = 0;
      for (let i = from; i <= to; i += 1) {
        const v = s.values[i];
        if (v == null) {
          pen = false;
          continue;
        }
        const x = xOf(i);
        const y = clippedYOf(v);
        if (!pen) {
          ctx.moveTo(x, y);
          pen = true;
        } else if (step) {
          ctx.lineTo(x, prevY);
          ctx.lineTo(x, y);
        } else {
          ctx.lineTo(x, y);
        }
        prevY = y;
      }
      return { pen };
    };

    const strokeSeries = (ctx: CanvasRenderingContext2D, s: TimeSeries, color: string, from: number, to: number, lineWidth: number) => {
      ctx.strokeStyle = color;
      ctx.lineWidth = lineWidth;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      walkPath(ctx, s, from, to);
      ctx.stroke();
    };

    /** 分档配色：把可见范围切成连续同档位的段；低值段（警示色）最后画、压在正常段上面。 */
    const runs = levelOf
      ? (() => {
          const list: { from: number; to: number; level: RateLevel }[] = [];
          for (let i = i0; i <= i1; i += 1) {
            const level = levelOf(i);
            const last = list[list.length - 1];
            if (last && last.level === level) last.to = i;
            else list.push({ from: i, to: i, level });
          }
          return [...list].sort((a, b) => (a.level === "ok" ? 0 : 1) - (b.level === "ok" ? 0 : 1));
        })()
      : null;

    const gradients = new Map<RateLevel, CanvasGradient>();
    const levelGradient = (ctx: CanvasRenderingContext2D, level: RateLevel) => {
      let gradient = gradients.get(level);
      if (!gradient) {
        gradient = ctx.createLinearGradient(0, PAD.top, 0, PAD.top + plotH);
        const color = statusColors[level];
        gradient.addColorStop(0, `${color}${dark ? "1F" : "12"}`);
        gradient.addColorStop(1, `${color}00`);
        gradients.set(level, gradient);
      }
      return gradient;
    };

    // 主图
    const main = mainRef.current;
    if (main) {
      main.width = Math.round(width * dpr);
      main.height = Math.round(height * dpr);
      const ctx = main.getContext("2d");
      if (ctx) {
        ctx.scale(dpr, dpr);
        ctx.clearRect(0, 0, width, height);
        ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
        const rows = 4;
        for (let r = 0; r <= rows; r += 1) {
          const v = y0 + ((y1 - y0) * r) / rows;
          const y = yOf(v);
          ctx.strokeStyle = gridColor;
          ctx.beginPath();
          ctx.moveTo(PAD.left, y);
          ctx.lineTo(width - PAD.right, y);
          ctx.stroke();
          ctx.fillStyle = axisColor;
          ctx.textAlign = "right";
          ctx.textBaseline = "middle";
          ctx.fillText(yFormat(v), PAD.left - 6, y);
        }
        const ticks = Math.min(5, count);
        for (let k = 0; k < ticks; k += 1) {
          const i = i0 + Math.round(((i1 - i0) * k) / (ticks - 1));
          ctx.fillStyle = axisColor;
          ctx.textBaseline = "top";
          ctx.textAlign = k === 0 ? "left" : k === ticks - 1 ? "right" : "center";
          ctx.fillText(formatTime(times[i]), xOf(i), height - PAD.bottom + 6);
        }
        ctx.save();
        ctx.beginPath();
        ctx.rect(PAD.left - 4, 0, Math.max((plotW + 8) * reveal, 0), height);
        ctx.clip();
        if (runs) {
          const series0 = visible[0];
          if (series0) {
            for (const run of runs) {
              const to = Math.min(run.to + 1, i1);
              // 阶梯填充：沿段走一遍再落到横轴，低值段的填充才完整
              walkPath(ctx, series0, run.from, to);
              ctx.lineTo(xOf(to), PAD.top + plotH);
              ctx.lineTo(xOf(run.from), PAD.top + plotH);
              ctx.closePath();
              ctx.fillStyle = levelGradient(ctx, run.level);
              ctx.fill();
              ctx.strokeStyle = statusColors[run.level];
              ctx.lineWidth = 1.25;
              ctx.lineJoin = "round";
              walkPath(ctx, series0, run.from, to);
              ctx.stroke();
            }
          }
        } else {
          visible.forEach((s) => {
            strokeSeries(ctx, s, colorOf[series.indexOf(s)], i0, i1, 1.5);
          });
        }
        ctx.restore();
        if (hover != null && hover >= i0 && hover <= i1) {
          const x = xOf(hover);
          ctx.strokeStyle = axisColor;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(x, PAD.top);
          ctx.lineTo(x, height - PAD.bottom);
          ctx.stroke();
          const targets = runs && levelOf && visible[0] ? [{ s: visible[0], color: statusColors[levelOf(hover)] ?? axisColor }] : visible.map((s) => ({ s, color: colorOf[series.indexOf(s)] ?? axisColor }));
          for (const { s, color } of targets) {
            const v = s.values[hover];
            if (v == null || v < y0 || v > y1) continue;
            ctx.fillStyle = color;
            ctx.beginPath();
            ctx.arc(x, yOf(v), 3, 0, Math.PI * 2);
            ctx.fill();
          }
        }
      }
    }

    // 概览刷选条：与主图同一套数据与刻度，画全量范围的细线
    const mini = miniRef.current;
    if (mini) {
      mini.width = Math.round(width * dpr);
      mini.height = Math.round(MINI_HEIGHT * dpr);
      const ctx = mini.getContext("2d");
      if (ctx) {
        ctx.scale(dpr, dpr);
        ctx.clearRect(0, 0, width, MINI_HEIGHT);
        // 概览条与主图同刻度：所见即所得
        const miniY = (v: number) => Math.min(Math.max(4 + (1 - (v - y0) / (y1 - y0)) * (MINI_HEIGHT - 8), 2), MINI_HEIGHT - 2);
        const miniX = (i: number) => PAD.left + (i / Math.max(count - 1, 1)) * plotW;
        ctx.save();
        if (runs && visible[0]) {
          for (const run of [...runs].sort((a, b) => a.from - b.from)) {
            ctx.strokeStyle = statusColors[run.level];
            ctx.lineWidth = 1;
            walkPath(ctx, visible[0], run.from, Math.min(run.to + 1, count - 1));
            ctx.stroke();
          }
        } else {
          visible.forEach((s) => {
            strokeSeries(ctx, s, colorOf[series.indexOf(s)], 0, count - 1, 1);
          });
        }
        ctx.restore();
        const xs = miniX(i0);
        const xe = miniX(i1);
        ctx.fillStyle = dark ? "rgba(255,255,255,0.55)" : "rgba(0,0,0,0.18)";
        ctx.fillRect(PAD.left, 0, Math.max(xs - PAD.left, 0), MINI_HEIGHT);
        ctx.fillRect(xe, 0, Math.max(width - PAD.right - xe, 0), MINI_HEIGHT);
        ctx.strokeStyle = dark ? "rgba(255,255,255,0.6)" : "rgba(0,0,0,0.3)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(xs, 0);
        ctx.lineTo(xs, MINI_HEIGHT);
        ctx.moveTo(xe, 0);
        ctx.lineTo(xe, MINI_HEIGHT);
        ctx.stroke();
      }
    }
  }, [width, height, dark, axisColor, gridColor, palette, statusColors, times, series, visible, colorOf, step, yDomain, yFormat, levelOf, windowFrac, hover, reveal, i0, i1, span, count]);

  if (count < 2) {
    return (
      <p style={{ color: "var(--text-3)", fontSize: 13, margin: 0 }}>至少两个检测点后这里会出现趋势图；当前 {count} 个。</p>
    );
  }

  const indexFromPointer = (clientX: number, el: HTMLElement) => {
    const rect = el.getBoundingClientRect();
    const plotW = Math.max(rect.width - PAD.left - PAD.right, 1);
    const t = (clientX - rect.left - PAD.left) / plotW;
    return Math.min(count - 1, Math.max(0, t * (count - 1)));
  };

  const setWindowFromDrag = (f0: number, f1: number) => {
    const lo = Math.max(0, Math.min(f0, f1));
    const hi = Math.min(1, Math.max(f0, f1));
    setWindowFrac([lo, Math.max(hi, lo + minSpan)]);
  };

  const toggle = (name: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  const hoverX = hover != null ? PAD.left + ((hover - i0) / span) * (width - PAD.left - PAD.right) : 0;
  const tooltipRows = hover != null
    ? visible.map((s) => ({ color: colorOf[series.indexOf(s)], name: s.name, value: s.values[hover] == null ? "—" : yFormat(s.values[hover] as number) }))
    : [];
  const flipTooltip = hoverX > width * 0.62;
  const levelLegend = levelOf && levelLabels;

  return (
    <div ref={wrapRef} style={{ position: "relative", userSelect: "none" }} onDoubleClick={() => setWindowFrac(null)}>
      <canvas
        ref={mainRef}
        style={{ width: "100%", height, display: "block", cursor: "crosshair", touchAction: "none" }}
        onPointerMove={(e) => setHover(Math.round(indexFromPointer(e.clientX, e.currentTarget)))}
        onPointerLeave={() => setHover(null)}
      />
      {hover != null && (
        <div
          style={{
            position: "absolute",
            top: 8,
            ...(flipTooltip ? { right: width - hoverX + 12 } : { left: hoverX + 12 }),
            pointerEvents: "none",
            zIndex: 5,
          }}
        >
          <ChartBubble label={formatTime(times[hover])} rows={tooltipRows} />
        </div>
      )}
      <canvas
        ref={miniRef}
        style={{ width: "100%", height: MINI_HEIGHT, display: "block", marginTop: 6, cursor: "pointer", touchAction: "none" }}
        onPointerDown={(e) => {
          const el = e.currentTarget;
          if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
          rafRef.current = null;
          setReveal(1);
          try {
            el.setPointerCapture(e.pointerId);
          } catch {
            // 合成事件等场景没有真实 pointer，捕获失败也继续拖拽
          }
          const f = indexFromPointer(e.clientX, el) / Math.max(count - 1, 1);
          if (!windowFrac) {
            drag.current = { mode: "new", anchor: f };
            setWindowFromDrag(f, f);
            return;
          }
          const [start, end] = windowFrac;
          const grab = (EDGE_GRAB_PX * 2) / Math.max(el.getBoundingClientRect().width - PAD.left - PAD.right, 1);
          if (Math.abs(f - start) <= grab) drag.current = { mode: "start" };
          else if (Math.abs(f - end) <= grab) drag.current = { mode: "end" };
          else drag.current = { mode: "move", grab: f - start, length: end - start };
        }}
        onPointerMove={(e) => {
          const state = drag.current;
          if (!state) return;
          const f = indexFromPointer(e.clientX, e.currentTarget) / Math.max(count - 1, 1);
          if (state.mode === "new" && state.anchor != null) {
            setWindowFromDrag(state.anchor, f);
          } else if (state.mode === "start" && windowFrac) {
            setWindowFromDrag(f, windowFrac[1]);
          } else if (state.mode === "end" && windowFrac) {
            setWindowFromDrag(windowFrac[0], f);
          } else if (state.mode === "move" && windowFrac && state.grab != null && state.length != null) {
            const length = Math.max(state.length, minSpan);
            const next = Math.min(Math.max(f - state.grab, 0), 1 - length);
            setWindowFrac([next, next + length]);
          }
        }}
        onPointerUp={() => {
          drag.current = null;
        }}
      />
      <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 12px", paddingTop: 8 }}>
        {levelLegend
          ? (Object.keys(levelLabels!) as RateLevel[]).map((level) => (
              <span key={level} style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-3)" }}>
                <span aria-hidden style={{ width: 8, height: 8, borderRadius: 2, background: statusColors[level], flexShrink: 0 }} />
                {levelLabels![level]}
              </span>
            ))
          : series.map((s, index) => (
              <button
                key={s.name}
                type="button"
                onClick={() => toggle(s.name)}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  fontSize: 12,
                  color: "var(--text-2)",
                  background: "none",
                  border: "none",
                  padding: 0,
                  cursor: "pointer",
                  opacity: hidden.has(s.name) ? 0.4 : 1,
                }}
              >
                <span aria-hidden style={{ width: 8, height: 8, borderRadius: 2, background: colorOf[index], flexShrink: 0 }} />
                <span className="mono">{s.name}</span>
              </button>
            ))}
      </div>
    </div>
  );
}
