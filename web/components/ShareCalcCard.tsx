"use client";

import { formatCount, formatDiscount, formatPrice, formatTime, formatTokens } from "@/lib/format";
import { formatAmount } from "@/lib/calculator";
import { calculator, share } from "@/lib/copy";
import type { CalcPrices, CalcResult, PriceKey } from "@/lib/calculator";
import { DajuPeek } from "./DajuArt";
import { IconAlertCircle } from "./icons";
import { chartPanelStyle, SHARE_PALETTES, useShareFontStacks, type ShareTheme } from "./ShareSiteCard";

const BUCKET_LABEL = calculator.buckets;
const BUCKET_ORDER: PriceKey[] = ["input", "output", "cacheRead"];
/** 明细表头：与页面结果表同一套文案（copy.calculator.detail） */
const TABLE_COLUMNS = [
  calculator.detail.bucket,
  calculator.detail.unitPrice,
  calculator.detail.tokens,
  calculator.detail.subtotal,
  calculator.detail.share,
] as const;

function symbolOf(currency: string): string {
  if (currency === "CNY") return "¥";
  if (currency === "USD") return "$";
  return "";
}

/** 花费计算分享卡：纯 DOM、固定 1000px 宽、全部内联样式，由 ShareCalcButton 用
 *  html-to-image 栅格化成 PNG；支持亮/暗两套配色，与站点状况分享卡同一套规则。 */
export function ShareCalcCard({
  modelName,
  modelSub,
  sourceLabel,
  currency,
  prices,
  totalTokens,
  hitRate,
  result,
  missingCacheRead,
  domain,
  generatedAt,
  theme = "dark",
}: {
  /** 模型展示名：厂商目录名或站点价记录的模型名，由 Calculator 按选中项解析 */
  modelName: string;
  /** 厂商名（官方价模式）或分组名（站点价模式），可空 */
  modelSub: string;
  /** 价格来源标签：「厂商官方价」或「中转站价 · 站点名」 */
  sourceLabel: string;
  currency: string;
  /** 三档单价（每 100 万 token），缺失的档显示 — */
  prices: CalcPrices;
  totalTokens: number;
  hitRate: number;
  result: CalcResult;
  /** 缓存命中价没填时命中的 token 不计费，图上要挑明，免得看图的人把总价当全量 */
  missingCacheRead: boolean;
  /** 监控站自己的域名，用于分享回流 */
  domain: string;
  /** 生成时刻（秒级时间戳），由调用方传入避免 SSR/客户端差异 */
  generatedAt: number;
  theme?: ShareTheme;
}) {
  const c = SHARE_PALETTES[theme];
  const { sans: sansStack, mono: monoStack } = useShareFontStacks();
  const symbol = symbolOf(currency);
  // 占比条与图例只画已计费的档；没填价的档在明细表里标注「未计入」
  const billed = result.lines.filter((line) => line.share !== null);
  let host = domain;
  try {
    host = new URL(domain).host || domain;
  } catch {
    // domain 还没取到（SSR 初值为空串）时保持原样
  }
  const priceChip = (key: PriceKey) => {
    const value = prices[key];
    return (
      <div
        key={key}
        style={{
          flex: 1,
          minWidth: 0,
          background: c.panel2,
          borderRadius: 10,
          padding: "12px 16px",
          display: "flex",
          flexDirection: "column",
          gap: 4,
        }}
      >
        <span style={{ fontSize: 11.5, color: c.faint }}>{BUCKET_LABEL[key]}</span>
        <span style={{ fontFamily: monoStack, fontVariantNumeric: "tabular-nums", fontSize: 16.5, fontWeight: 550, color: c.text }}>
          {value != null && value >= 0 ? `${symbol}${formatPrice(value)}` : "—"}
        </span>
      </div>
    );
  };

  return (
    <div
      style={{
        width: 1000,
        boxSizing: "border-box",
        padding: "44px 48px 34px",
        background: c.bg,
        color: c.text,
        fontFamily: sansStack,
        display: "flex",
        flexDirection: "column",
        gap: 26,
      }}
    >
      {/* 品牌头：与站点状况分享卡同款——奶油底橘猫探头 logo + 等宽域名 */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <svg width={40} height={40} viewBox="0 0 64 64" role="img" aria-label="llmprices.cn" style={{ flexShrink: 0 }}>
              <DajuPeek shape="square" />
            </svg>
            <span style={{ fontFamily: monoStack, fontSize: 20, fontWeight: 600, lineHeight: 1, letterSpacing: "0.02em" }}>llmprices.cn</span>
          </div>
          <span style={{ fontSize: 13.5, color: c.muted, maxWidth: 560, lineHeight: 1.6 }}>{share.brandDesc}</span>
        </div>
        <span style={{ fontFamily: monoStack, fontSize: 14, color: c.muted, background: c.panel2, padding: "7px 14px", borderRadius: 999 }}>
          {host}
        </span>
      </div>

      <div style={{ height: 0, borderTop: c.divider }} />

      {/* 模型与来源 */}
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 20 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, minWidth: 0 }}>
            <span style={{ fontSize: 34, fontWeight: 600, lineHeight: 1.15, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {modelName || "花费估算"}
            </span>
            {modelSub && <span style={{ fontSize: 13.5, color: c.faint, flexShrink: 0 }}>{modelSub}</span>}
          </div>
          <span style={{ fontSize: 13.5, color: c.muted }}>AI 花费估算 · 每 100 万 token 单价</span>
        </div>
        <span style={{ fontFamily: monoStack, fontSize: 13.5, color: c.text, background: c.panel2, padding: "7px 14px", borderRadius: 6, flexShrink: 0 }}>
          {sourceLabel}
        </span>
      </div>

      {/* KPI：总价是主数，用量与命中率说明口径 */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 14 }}>
        {[
          {
            label: "预计总花费",
            value: `${symbol}${formatAmount(result.total)}`,
            color: c.accent,
          },
          { label: "总用量", value: `${formatTokens(totalTokens)} tokens`, color: c.text },
          { label: "缓存命中率", value: `${hitRate}%`, color: c.text },
        ].map((kpi) => (
          <div
            key={kpi.label}
            style={{
              minWidth: 0,
              background: c.panel,
              border: c.panelBorder,
              borderRadius: 12,
              padding: "20px 22px 19px",
            }}
          >
            <span style={{ fontSize: 12, color: c.faint }}>{kpi.label}</span>
            <span
              style={{
                display: "block",
                fontFamily: monoStack,
                fontVariantNumeric: "tabular-nums",
                fontSize: 28,
                fontWeight: 550,
                letterSpacing: "-0.02em",
                lineHeight: 1.2,
                marginTop: 5,
                color: kpi.color,
              }}
            >
              {kpi.value}
            </span>
          </div>
        ))}
      </div>

      {/* 单价口径：三档并排，没填的档显示 — */}
      <div style={{ display: "flex", gap: 14 }}>
        {BUCKET_ORDER.map((key) => priceChip(key))}
      </div>

      {/* 花费构成：占比条 + 明细表，与页面结果表同一份数据 */}
      <div style={chartPanelStyle(c)}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            {/* nowrap：截图时字体回退宽度不稳，标题末字会被挤到第二行 */}
            <span style={{ fontSize: 14.5, fontWeight: 600, whiteSpace: "nowrap" }}>花费构成 · 各计费项</span>
            <span style={{ fontFamily: monoStack, fontSize: 12.5, color: c.muted, whiteSpace: "nowrap" }}>
              合计 {symbol}
              {formatAmount(result.total)}
            </span>
          </div>
          <div style={{ display: "flex", height: 12, borderRadius: 6, overflow: "hidden", background: c.pill }} aria-hidden>
            {billed.map((line, index) => (
              <span
                key={line.key}
                style={{
                  width: `${(line.share ?? 0) * 100}%`,
                  background: c.chartPalette[index % c.chartPalette.length],
                }}
              />
            ))}
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "2px 14px" }}>
            {billed.map((line, index) => (
              <span key={line.key} style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11.5, color: c.muted, fontFamily: monoStack }}>
                <span aria-hidden style={{ width: 8, height: 8, borderRadius: 999, background: c.chartPalette[index % c.chartPalette.length], flexShrink: 0 }} />
                {BUCKET_LABEL[line.key]} {formatDiscount(line.share)}
              </span>
            ))}
          </div>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr>
                {(TABLE_COLUMNS as readonly string[]).map((label, index) => (
                  <th
                    key={label}
                    style={{
                      textAlign: index === 0 ? "left" : "right",
                      fontSize: 11.5,
                      fontWeight: 500,
                      color: c.faint,
                      padding: "8px 4px 6px",
                      borderBottom: c.divider,
                    }}
                  >
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.lines.map((line) => {
                const unbilled = line.subtotal === null;
                return (
                  <tr key={line.key} style={unbilled ? { opacity: 0.55 } : undefined}>
                    <td style={{ padding: "9px 4px", borderBottom: c.divider, color: c.text }}>
                      {BUCKET_LABEL[line.key]}
                      {unbilled && <span style={{ marginLeft: 6, fontSize: 11, color: c.faint }}>未计入</span>}
                    </td>
                    <td style={{ padding: "9px 4px", borderBottom: c.divider, color: c.muted, fontFamily: monoStack, fontVariantNumeric: "tabular-nums", textAlign: "right" }}>
                      {line.unitPrice !== null ? `${symbol}${formatPrice(line.unitPrice)}` : "—"}
                    </td>
                    <td style={{ padding: "9px 4px", borderBottom: c.divider, color: c.muted, fontFamily: monoStack, fontVariantNumeric: "tabular-nums", textAlign: "right" }}>
                      {formatCount(line.tokens)}
                    </td>
                    <td style={{ padding: "9px 4px", borderBottom: c.divider, color: c.text, fontFamily: monoStack, fontVariantNumeric: "tabular-nums", textAlign: "right" }}>
                      {line.subtotal !== null ? `${symbol}${formatAmount(line.subtotal)}` : "—"}
                    </td>
                    <td style={{ padding: "9px 4px", borderBottom: c.divider, color: c.muted, fontFamily: monoStack, fontVariantNumeric: "tabular-nums", textAlign: "right" }}>
                      {line.share !== null ? formatDiscount(line.share) : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {missingCacheRead && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, background: c.redBg, color: c.redText, borderRadius: 8, padding: "10px 14px", fontSize: 13 }}>
              <IconAlertCircle size={14} />
              <span>{calculator.missingCache}</span>
            </div>
          )}
        </div>

      {/* 页脚：站内主按钮同款做回流 CTA，右侧时间戳 */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 2 }}>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            height: 34,
            padding: "0 16px",
            borderRadius: 4,
            background: c.accent,
            color: c.accentContrast,
            fontSize: 13.5,
            fontWeight: 550,
          }}
        >
          去 {host} 算你的花费
        </span>
        <span style={{ fontFamily: monoStack, fontSize: 13, color: c.faint }}>数据截至 {formatTime(generatedAt)}</span>
      </div>
    </div>
  );
}
