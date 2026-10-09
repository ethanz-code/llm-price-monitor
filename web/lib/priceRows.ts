/** 价格行的合并规则：OverviewTable 与首页最新快照共用，保证"同站点同模型合并为一行"的口径一致。 */

import { effectiveCnyPrice, effectivePrice } from "./format";
import type { OverviewRecord } from "./types";

/** 模型名归一：小写并去掉空格/连字符/下划线/点。
 *  不同站点对同一模型的写法经常不同（如 "GPT-5.6 Sol" 与 "gpt-5.6-sol"），归一后才能按语义合并。 */
export function canonicalModel(model: string): string {
  return model.toLowerCase().replace(/[\s\-_.]+/g, "");
}

/** 有效价 = 顶层价格或阶梯档价任一可得；无效行（需认证/无数据）在时间排序中沉底。 */
export function hasUsablePrice(row: OverviewRecord): boolean {
  return effectivePrice(row, "input_price") != null || effectivePrice(row, "output_price") != null;
}

/** 最新快照的展示行序：有可用价在前，同状态按采集时间倒序；不再按站点+模型折叠，各分组各占一行。 */
export function sortSnapshotRows(records: OverviewRecord[]): OverviewRecord[] {
  return [...records].sort((a, b) => {
    if (hasUsablePrice(a) !== hasUsablePrice(b)) return hasUsablePrice(a) ? -1 : 1;
    return b.captured_at - a.captured_at;
  });
}

/** 综合价（排序用）：输入 3 : 输出 1 加权，统一折算 RMB；缺一项用另一项，都缺返回 null。 */
export function blendedCnyPrice(row: OverviewRecord, rate: number | null): number | null {
  const input = effectiveCnyPrice(row, "input_price", rate);
  const output = effectiveCnyPrice(row, "output_price", rate);
  if (input != null && output != null) return (input * 3 + output) / 4;
  return input ?? output;
}

/** 智能排序（总览表默认行序）：有价在前 → 综合价低的在前（输入 3 : 输出 1 加权）→
 *  扣分少的在前（调用方按渠道成功率、缺渠道/缺公告、信息完整度扣分）；
 *  折扣与抓取时间破平。规则价与确认价同等对待；点表头单列排序循环回"无排序"即回到此序。 */
export function smartOrderRows(
  rows: OverviewRecord[],
  penaltyOf: (row: OverviewRecord) => number,
  rate: number | null,
): OverviewRecord[] {
  return [...rows].sort((a, b) => {
    const pricedA = hasUsablePrice(a);
    const pricedB = hasUsablePrice(b);
    if (pricedA !== pricedB) return pricedA ? -1 : 1;
    if (pricedA) {
      const blendA = blendedCnyPrice(a, rate) ?? Number.POSITIVE_INFINITY;
      const blendB = blendedCnyPrice(b, rate) ?? Number.POSITIVE_INFINITY;
      if (blendA !== blendB) return blendA - blendB;
      const penaltyA = penaltyOf(a);
      const penaltyB = penaltyOf(b);
      if (penaltyA !== penaltyB) return penaltyA - penaltyB;
      const discountA = a.discount?.input ?? 9;
      const discountB = b.discount?.input ?? 9;
      if (discountA !== discountB) return discountA - discountB;
    }
    return b.captured_at - a.captured_at || a.site_id.localeCompare(b.site_id);
  });
}
