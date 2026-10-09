import { discountTone, formatDiscount, toneText } from "@/lib/format";

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
function DiscountBar({ label, value }: { label: string; value: number | null | undefined }) {
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

/** 紧凑折扣数值：语义色直读（≤50% 绿、≤80% 黄、其余红），不再画色点。 */
function DiscountValue({ value }: { value: number }) {
  return (
    <span className="mono" style={{ fontSize: 12.5, color: toneText(discountTone(value)) }}>
      {formatDiscount(value)}
    </span>
  );
}

/** 首页快照表的紧凑折扣：输入/输出折扣相同时合并为一个数值（最常见的情形，一行只读一个数），
 *  不同时写全「输入/输出」并列；数值直接用折扣语义色，深浅一眼可辨。 */
function DiscountCompact({ discount }: { discount: { input: number | null; output: number | null } }) {
  const { input, output } = discount;
  if (input != null && output != null && input === output) {
    return (
      <span title={`输入与输出折扣相同，均为厂商原价的 ${formatDiscount(input)}`}>
        <DiscountValue value={input} />
      </span>
    );
  }
  return (
    <span style={{ display: "inline-flex", alignItems: "baseline", gap: 10, whiteSpace: "nowrap" }}>
      {input != null && (
        <span style={{ display: "inline-flex", alignItems: "baseline", gap: 4 }}>
          <span style={{ fontSize: 12, color: "var(--text-3)" }}>输入</span>
          <DiscountValue value={input} />
        </span>
      )}
      {output != null && (
        <span style={{ display: "inline-flex", alignItems: "baseline", gap: 4 }}>
          <span style={{ fontSize: 12, color: "var(--text-3)" }}>输出</span>
          <DiscountValue value={output} />
        </span>
      )}
    </span>
  );
}

/** 输入/输出两条折扣条形；两者皆空时显示弱化的 —。
 *  compact = 首页快照表单行版（相同折扣合并、语义色直读），默认堆叠条形（总览表等宽松表格）。 */
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
    return <DiscountCompact discount={discount} />;
  }
  return (
    <span className="disc-cell">
      <DiscountBar label="输入" value={discount.input} />
      <DiscountBar label="输出" value={discount.output} />
    </span>
  );
}
