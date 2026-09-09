"use client";

import Link from "next/link";
import type { ChannelDot } from "@/lib/channelStatus";
import { formatTime } from "@/lib/format";

// 渠道点阵规格与总览页一致：3 行 × 15 列小点 = 最近 45 次检测
const DOT_COLS = 15;
const DOT_ROWS = 3;

/** 渠道检测点阵：每行 15 个点是最近 15 次检测，绿点正常、红点异常；无数据返回 —。 */
export function ChannelDotMatrix({
  dots,
  name,
  href,
}: {
  dots: ChannelDot[] | null | undefined;
  /** 悬停提示里显示的主体名（渠道/分组名） */
  name?: string;
  href?: string;
}) {
  if (!dots || dots.length === 0) return <span style={{ color: "var(--text-3)" }}>—</span>;
  const recent = dots.slice(-DOT_COLS * DOT_ROWS);
  // 补齐成整行 × 整列：老数据不足时前面补透明占位，保持列纵向对齐
  const padded = (
    Array.from({ length: DOT_COLS * DOT_ROWS - recent.length }, () => null) as (ChannelDot | null)[]
  ).concat(recent);
  const body = Array.from({ length: DOT_ROWS }, (_, line) => (
    <span key={line} className="ch-line-dots">
      {padded.slice(line * DOT_COLS, (line + 1) * DOT_COLS).map((dot, index) =>
        dot ? (
          <span
            key={index}
            aria-hidden
            title={`${name ?? ""}：${dot.status}${dot.at != null ? ` · ${formatTime(dot.at)}` : ""}`}
            className={`ch-mini ${dot.ok ? "ch-ok" : "ch-down"}`}
          />
        ) : (
          <span key={index} aria-hidden className="ch-mini" style={{ visibility: "hidden" }} />
        ),
      )}
    </span>
  ));
  if (!href) return <span>{body}</span>;
  return (
    <Link href={href} className="ch-matrix" title="每行 15 个点是本分组最近 15 次检测，点击查看趋势图">
      {body}
    </Link>
  );
}
