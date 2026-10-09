/** 价格行的合并规则：OverviewTable 与首页最新快照共用，保证"同站点同模型合并为一行"的口径一致。 */

import { effectiveCnyPrice, effectivePrice } from "./format";
import type { OverviewRecord } from "./types";

/** 模型名归一：小写并去掉空格/连字符/下划线（与后端 model_key 同规则，点号保留——
 *  "GLM-5.2" 与 "GLM52" 在后端目录里就是两个键，前端分组不擅自合并）。
 *  不同站点对同一模型的写法经常不同（如 "GPT-5.6 Sol" 与 "gpt-5.6-sol"），归一后才能按语义合并。 */
export function canonicalModel(model: string): string {
  return model.toLowerCase().replace(/[\s\-_]+/g, "");
}

/** 有效价 = 顶层价格或阶梯档价任一可得；无效行（需认证/无数据）在时间排序中沉底。 */
export function hasUsablePrice(row: OverviewRecord): boolean {
  return effectivePrice(row, "input_price") != null || effectivePrice(row, "output_price") != null;
}

/** 首页精选行序：综合价低的在前（同价按采集更新的在前），让"各模型全站最低价"直接按价读。
 *  入参约定来自 lowestPriceRowPerModel（每行都有可用价），缺综合价的 +∞ 沉底是兜底分支。 */
export function orderByCheapestPrice(rows: OverviewRecord[], rate: number | null): OverviewRecord[] {
  const blendOf = (row: OverviewRecord) => blendedCnyPrice(row, rate) ?? Number.POSITIVE_INFINITY;
  return [...rows].sort((a, b) => blendOf(a) - blendOf(b) || b.captured_at - a.captured_at);
}

/** 综合价（排序用）：输入 3 : 输出 1 加权，统一折算 RMB；缺一项用另一项，都缺返回 null。 */
export function blendedCnyPrice(row: OverviewRecord, rate: number | null): number | null {
  const input = effectiveCnyPrice(row, "input_price", rate);
  const output = effectiveCnyPrice(row, "output_price", rate);
  if (input != null && output != null) return (input * 3 + output) / 4;
  return input ?? output;
}

/** 首页精选行：每个模型（canonicalModel 归一分组）只保留一行——组内有可用价的行里综合价最低的那条
 *  （输入 3 : 输出 1 折 RMB，同价取采集更新的），展示名取组内出现最多的写法（与总览表口径一致）；
 *  全组没有可用价的模型整个不出现，首页精选只放拿得到真实价格的模型。 */
export function lowestPriceRowPerModel(records: OverviewRecord[], rate: number | null): OverviewRecord[] {
  const groups = new Map<string, OverviewRecord[]>();
  for (const row of records) {
    const key = canonicalModel(row.model);
    const list = groups.get(key);
    if (list) list.push(row);
    else groups.set(key, [row]);
  }
  const picked: OverviewRecord[] = [];
  for (const rows of groups.values()) {
    const priced = rows.filter(hasUsablePrice);
    if (priced.length === 0) continue;
    const blendOf = (row: OverviewRecord) => blendedCnyPrice(row, rate) ?? Number.POSITIVE_INFINITY;
    const best = [...priced].sort((a, b) => blendOf(a) - blendOf(b) || b.captured_at - a.captured_at)[0];
    const names = new Map<string, number>();
    for (const row of rows) names.set(row.model, (names.get(row.model) ?? 0) + 1);
    const displayName = [...names.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0][0];
    picked.push({ ...best, model: displayName });
  }
  return picked;
}

/** 可靠性扣分对综合价的最大影响：满档扣分让排序价虚增 20%，可靠性与价格信号的权衡上限。 */
export const RELIABILITY_CAP = 0.2;

/** 可靠性加权综合价（智能排序主键）：综合价 × (1 + 20% × 归一化扣分)；缺综合价按 +∞ 沉底。 */
function reliabilityWeightedPrice(
  row: OverviewRecord,
  reliabilityDeficit: number,
  rate: number | null,
): number {
  const blend = blendedCnyPrice(row, rate);
  if (blend == null) return Number.POSITIVE_INFINITY;
  return blend * (1 + RELIABILITY_CAP * reliabilityDeficit);
}

/** 智能排序（总览表默认行序）：有价在前 → 可靠性加权综合价低的在前
 *  （综合价 × (1 + 20% × 归一化扣分)，扣分由调用方按渠道健康与数据滞后计算、0–1 归一，
 *  让渠道挂掉/数据过期的站点真实下沉，但最多让位 20% 不淹没价格信号）→
 *  折扣与抓取时间破平。规则价与确认价同等对待；点表头单列排序循环回"无排序"即回到此序。 */
export function smartOrderRows(
  rows: OverviewRecord[],
  reliabilityDeficitOf: (row: OverviewRecord) => number,
  rate: number | null,
): OverviewRecord[] {
  return [...rows].sort((a, b) => {
    const pricedA = hasUsablePrice(a);
    const pricedB = hasUsablePrice(b);
    if (pricedA !== pricedB) return pricedA ? -1 : 1;
    if (pricedA) {
      const keyA = reliabilityWeightedPrice(a, reliabilityDeficitOf(a), rate);
      const keyB = reliabilityWeightedPrice(b, reliabilityDeficitOf(b), rate);
      if (keyA !== keyB) return keyA - keyB;
      const discountA = a.discount?.input ?? 9;
      const discountB = b.discount?.input ?? 9;
      if (discountA !== discountB) return discountA - discountB;
    }
    return b.captured_at - a.captured_at || a.site_id.localeCompare(b.site_id);
  });
}
