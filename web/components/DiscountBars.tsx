import { discountTone, formatDiscount } from "@/lib/format";

/** 折扣条渐变色：0 全绿 → 0.5 纯黄 → 1 全红，在语义 tone 色之间按数值连续插值。
 *  低折扣段按平方根布色（1%→14% 黄混、5%→32%、25%→71%），把常见 1–5 折数据的颜色差异放大到肉眼可辨；
 *  亮暗主题都引用同一组 --tone-*-text 变量，不另设档位类。 */
function discountFillColor(value: number): string {
  const v = Math.min(Math.max(value, 0), 1);
  if (v <= 0.5) {
    const t = Math.round(Math.sqrt(v / 0.5) * 100);
    return `color-mix(in srgb, var(--tone-yellow-text) ${t}%, var(--tone-green-text))`;
  }
  const t = Math.round(((v - 0.5) / 0.5) * 100);
  return `color-mix(in srgb, var(--tone-red-text) ${t}%, var(--tone-yellow-text))`;
}

/** 单条折扣的迷你条形：数值 + 相对厂商价的比例条，填充色随折扣值连续渐变（折扣越深越绿、越接近原价越红）。 */
export function DiscountBar({ label, value }: { label: string; value: number | null | undefined }) {
  if (value === null || value === undefined) return null;
  const pct = Math.round(Math.min(Math.max(value, 0), 1) * 100);
  return (
    <span className="disc-bar">
      <span className="disc-bar-head">
        <span className="disc-bar-label">{label}</span>
        <span className="mono disc-bar-val">{formatDiscount(value)}</span>
      </span>
      <span className="disc-bar-track" aria-hidden>
        <span
          className="disc-bar-fill"
          style={{
            width: `${Math.max(pct, 2)}%`,
            background: discountFillColor(value),
            // 文字色原为正文对比度设计，小条只做辅助信号，降强度让数字当主信息
            opacity: 0.7,
          }}
        />
      </span>
    </span>
  );
}

/** 汇总区间条：各站点折扣的最低–最高范围画在 0–100% 刻度上，竖线标均值位置。
 *  颜色沿用均值折扣的语义 tone，与逐站点折扣条一致。 */
export function DiscountRangeBar({ min, max, avg }: { min: number; max: number; avg: number }) {
  const tone = discountTone(avg);
  const scale = (value: number) => Math.min(Math.max(value, 0), 1) * 100;
  return (
    <span className="disc-range" aria-hidden>
      <span
        className={`disc-range-fill tone-${tone}`}
        style={{ left: `${scale(min)}%`, width: `${Math.max(scale(max) - scale(min), 2)}%` }}
      />
      <span className={`disc-range-avg tone-${tone}`} style={{ left: `${scale(avg)}%` }} />
    </span>
  );
}

/** 紧凑折扣芯片：色点表达折扣深浅（与条形同一套渐变色），文字单行，给行高敏感的表用。 */
function DiscountChip({ label, value }: { label: string; value: number | null | undefined }) {
  if (value === null || value === undefined) return null;
  return (
    <span className="disc-chip">
      <span aria-hidden className="disc-chip-dot" style={{ background: discountFillColor(value) }} />
      <span className="disc-chip-label">{label}</span>
      <span className="mono disc-chip-val">{formatDiscount(value)}</span>
    </span>
  );
}

/** 输入/输出两条折扣条形；两者皆空时显示弱化的 —。
 *  compact = 单行芯片版（首页快照表），默认堆叠条形（总览表等宽松表格）。 */
export function DiscountBars({
  discount,
  compact = false,
}: {
  discount: { input: number | null; output: number | null } | null | undefined;
  compact?: boolean;
}) {
  if (!discount || (discount.input === null && discount.output === null)) {
    return <span style={{ color: "var(--text-3)" }}>—</span>;
  }
  if (compact) {
    return (
      <span className="disc-chips">
        <DiscountChip label="入" value={discount.input} />
        <DiscountChip label="出" value={discount.output} />
      </span>
    );
  }
  return (
    <span className="disc-cell">
      <DiscountBar label="输入" value={discount.input} />
      <DiscountBar label="输出" value={discount.output} />
    </span>
  );
}
