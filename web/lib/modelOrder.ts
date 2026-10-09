/** 模型与厂商的目录口径排序：发布日期倒序（新→旧）、同日期按名称、没日期垫底按名称。
 *  监控模型下拉、计算页候选、中转站定价模型列表、厂商定价页表格共用同一套，
 *  任何地方看到的模型顺序都一致；厂商块按各家最新发布日期倒序，新模型多的厂商靠前。 */

import type { CatalogEntry } from "./types";

export interface OrderedModel {
  name: string;
  release?: string | null;
}

/** 模型级比较：发布日期倒序，同日期按名称，没日期的垫底再按名称。 */
export function compareByReleaseDesc(a: OrderedModel, b: OrderedModel): number {
  return (b.release ?? "").localeCompare(a.release ?? "") || a.name.localeCompare(b.name);
}

/** 厂商 → 该家最新发布日期（没日期的条目不参与，全没有则为空串垫底）。 */
export function latestReleaseByVendor(models: Record<string, CatalogEntry>): Map<string, string> {
  const latest = new Map<string, string>();
  for (const entry of Object.values(models)) {
    if (!entry?.found) continue;
    const release = entry.release_date ?? "";
    const current = latest.get(entry.vendor);
    if (current === undefined || release.localeCompare(current) > 0) latest.set(entry.vendor, release);
  }
  return latest;
}

/** 厂商块比较（基于 latestReleaseByVendor 的结果）：各家最新发布倒序，同状态按厂商名。 */
export function vendorBlockCompare(latest: Map<string, string>) {
  return (a: string, b: string) =>
    (latest.get(b) ?? "").localeCompare(latest.get(a) ?? "") || a.localeCompare(b);
}
