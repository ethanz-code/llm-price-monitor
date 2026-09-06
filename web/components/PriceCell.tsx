"use client";

import { currencySymbol, formatPrice, tieredPrices, toCnyPrice, TIERED_PRICE_TIP } from "@/lib/format";

interface PriceLike {
  input_price: number | null;
  output_price: number | null;
  unit: string;
  metadata?: { pricing_rules?: unknown; [key: string]: unknown } | null;
}

/** 价格格：普通价单行数字；阶梯价逐档一行（上下文范围 + 单价），单档视为唯一价不标范围。
 *  传 rate 时站点价统一按 RMB 展示（USD 乘汇率折算，CNY 原样）；无法折算回落原币。 */
export function PriceCell({
  row,
  field,
  rate,
}: {
  row: PriceLike;
  field: "input_price" | "output_price";
  rate?: number | null;
}) {
  const tiers = tieredPrices(row, field);
  const values = tiers.length > 0 ? tiers.map((tier) => tier.price) : [row[field]];
  const converted = values.map((value) => toCnyPrice(value, row.unit, rate));
  const allConverted = converted.every((value) => value !== null);
  const display = allConverted ? converted.map((value) => value as number) : values;
  const symbol = allConverted ? "¥" : currencySymbol(row.unit);
  if (tiers.length <= 1) {
    return (
      <span
        className="mono num"
        title={tiers.length === 1 ? TIERED_PRICE_TIP : undefined}
      >
        {symbol}
        {formatPrice(display[0])}
      </span>
    );
  }
  return (
    <span className="tier-price" title={TIERED_PRICE_TIP}>
      {tiers.map((tier, index) => (
        <span key={index} className="tier-line">
          <span className="tier-label">{tier.label}</span>
          <span className="mono num">
            {symbol}
            {formatPrice(display[index])}
          </span>
        </span>
      ))}
    </span>
  );
}
