/** 价格行的合并规则：OverviewTable 与首页最新快照共用，保证"同站点同模型合并为一行"的口径一致。 */

import { effectiveCnyPrice, effectivePrice } from "./format";
import type { OverviewRecord } from "./types";

/** 模型名归一：小写并去掉空格/连字符/下划线/点。
 *  不同站点对同一模型的写法经常不同（如 "GPT-5.6 Sol" 与 "gpt-5.6-sol"），归一后才能按语义合并。 */
export function canonicalModel(model: string): string {
  return model.toLowerCase().replace(/[\s\-_.]+/g, "");
}

/** 行合并键：同站点 + 同模型（归一后）+ 同单位视为同一模型的变体（不同分组/倍率）；单位不同不并组。 */
export function modelRowKey(row: OverviewRecord): string {
  return `${row.site_id}:${canonicalModel(row.model)}:${row.unit}`;
}

/** 有效价 = 顶层价格或阶梯档价任一可得；无效行（需认证/无数据）在时间排序中沉底。 */
export function hasUsablePrice(row: OverviewRecord): boolean {
  return effectivePrice(row, "input_price") != null || effectivePrice(row, "output_price") != null;
}

/** 站点+模型 合并为一行：代表行取价格最低的一条，其余记录展开后可见；单位不同的记录不并组。 */
export function mergeModelRows(records: OverviewRecord[]): {
  parentRows: OverviewRecord[];
  childRowsOf: Map<string, OverviewRecord[]>;
} {
  const groups = new Map<string, OverviewRecord[]>();
  for (const row of records) {
    const key = modelRowKey(row);
    const list = groups.get(key);
    if (list) list.push(row);
    else groups.set(key, [row]);
  }
  // 代表行：有可用价者优先；输入价最低（阶梯取最低档），其次输出价最低；再同取最新
  const better = (a: OverviewRecord, b: OverviewRecord) => {
    if (hasUsablePrice(a) !== hasUsablePrice(b)) return hasUsablePrice(a);
    const inputA = effectivePrice(a, "input_price") ?? Number.POSITIVE_INFINITY;
    const inputB = effectivePrice(b, "input_price") ?? Number.POSITIVE_INFINITY;
    if (inputA !== inputB) return inputA < inputB;
    const outputA = effectivePrice(a, "output_price") ?? Number.POSITIVE_INFINITY;
    const outputB = effectivePrice(b, "output_price") ?? Number.POSITIVE_INFINITY;
    if (outputA !== outputB) return outputA < outputB;
    return a.captured_at > b.captured_at;
  };
  const parentRows: OverviewRecord[] = [];
  const childRowsOf = new Map<string, OverviewRecord[]>();
  for (const [key, list] of groups) {
    const best = list.reduce((acc, row) => (better(row, acc) ? row : acc), list[0]);
    parentRows.push(best);
    const rest = list.filter((row) => row !== best);
    if (rest.length > 0) childRowsOf.set(key, rest);
  }
  // 有效价行在前（时间倒序），需认证/无数据的行沉底
  parentRows.sort((a, b) => {
    if (hasUsablePrice(a) !== hasUsablePrice(b)) return hasUsablePrice(a) ? -1 : 1;
    return b.captured_at - a.captured_at;
  });
  return { parentRows, childRowsOf };
}

/** 综合价（排序用）：输入 3 : 输出 1 加权，统一折算 RMB；缺一项用另一项，都缺返回 null。 */
export function blendedCnyPrice(row: OverviewRecord, rate: number | null): number | null {
  const input = effectiveCnyPrice(row, "input_price", rate);
  const output = effectiveCnyPrice(row, "output_price", rate);
  if (input != null && output != null) return (input * 3 + output) / 4;
  return input ?? output;
}

/** 智能排序（总览表默认行序）：有价在前 → 扣分少的在前（调用方按渠道成功率、缺渠道/缺公告扣分）→
 *  综合价低的在前；折扣与抓取时间破平。规则价与确认价同等对待；点表头单列排序循环回"无排序"即回到此序。 */
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
      const penaltyA = penaltyOf(a);
      const penaltyB = penaltyOf(b);
      if (penaltyA !== penaltyB) return penaltyA - penaltyB;
      const blendA = blendedCnyPrice(a, rate) ?? Number.POSITIVE_INFINITY;
      const blendB = blendedCnyPrice(b, rate) ?? Number.POSITIVE_INFINITY;
      if (blendA !== blendB) return blendA - blendB;
      const discountA = a.discount?.input ?? 9;
      const discountB = b.discount?.input ?? 9;
      if (discountA !== discountB) return discountA - discountB;
    }
    return b.captured_at - a.captured_at || a.site_id.localeCompare(b.site_id);
  });
}
