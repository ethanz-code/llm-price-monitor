import { discountTone, formatDiscount } from "@/lib/format";

/** 单条折扣的迷你条形：数值 + 相对厂商价的比例条，颜色沿用语义 tone。 */
export function DiscountBar({ label, value }: { label: string; value: number | null | undefined }) {
  if (value === null || value === undefined) return null;
  const tone = discountTone(value);
  const pct = Math.round(Math.min(Math.max(value, 0), 1) * 100);
  return (
    <span className="disc-bar">
      <span className="disc-bar-head">
        <span className="disc-bar-label">{label}</span>
        <span className="mono disc-bar-val">{formatDiscount(value)}</span>
      </span>
      <span className="disc-bar-track" aria-hidden>
        <span className={`disc-bar-fill tone-${tone}`} style={{ width: `${Math.max(pct, 2)}%` }} />
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

/** 输入/输出两条折扣条形；两者皆空时显示弱化的 —。 */
export function DiscountBars({
  discount,
}: {
  discount: { input: number | null; output: number | null } | null | undefined;
}) {
  if (!discount || (discount.input === null && discount.output === null)) {
    return <span style={{ color: "var(--text-3)" }}>—</span>;
  }
  return (
    <span className="disc-cell">
      <DiscountBar label="输入" value={discount.input} />
      <DiscountBar label="输出" value={discount.output} />
    </span>
  );
}
