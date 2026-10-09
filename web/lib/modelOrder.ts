/** 模型与厂商的目录口径排序。
 *
 *  两套口径，按界面形态分工：
 *  - 厂商分块表（厂商定价页两张表）：厂商块按各家最新发布日期倒序，新模型多的厂商靠前；
 *  - 模型清单下拉（站点管理监控模型多选、模型数据页模型选择）：厂商块按目录里厂商
 *    首次出现的顺序（权威清单序），厂商内发布日期倒序、同日期按名称，目录外模型垫底。
 *    两处下拉共用 catalogModelOrderIndex + modelOrderCompare，顺序保证一致。
 */

import type { CatalogEntry } from "./types";

export interface OrderedModel {
  name: string;
  release?: string | null;
}

/** 模型级比较：发布日期倒序，同日期按名称，没日期的垫底再按名称。 */
export function compareByReleaseDesc(a: OrderedModel, b: OrderedModel): number {
  return (b.release ?? "").localeCompare(a.release ?? "") || a.name.localeCompare(b.name);
}

/** 模型名归一键：去掉空格/横线/下划线并 casefold，"GPT-5.6 Sol" 与 "gpt_5_6_sol" 同键。 */
export function canonicalModel(model: string): string {
  return model.toLowerCase().replace(/[\s\-_]+/g, "");
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

/** 模型清单下拉的排序索引值：厂商序号（目录里该厂商首次出现的位置）+ 目录发布日期。 */
export interface ModelOrderEntry {
  vendorRank: number;
  release: string;
}

/** 目录 → 模型排序索引（归一键 → 排序依据）：厂商序号按目录 models 里厂商首次
 *  出现的顺序，没日期的条目 release 为空串在本厂商块内垫底。 */
export function catalogModelOrderIndex(models: Record<string, CatalogEntry>): Record<string, ModelOrderEntry> {
  const rankByVendor = new Map<string, number>();
  const index: Record<string, ModelOrderEntry> = {};
  for (const entry of Object.values(models)) {
    const key = entry?.model ? canonicalModel(entry.model) : "";
    if (!key) continue;
    if (entry.vendor && !rankByVendor.has(entry.vendor)) rankByVendor.set(entry.vendor, rankByVendor.size);
    const rank = entry.vendor ? rankByVendor.get(entry.vendor) : undefined;
    index[key] = {
      vendorRank: rank ?? Number.MAX_SAFE_INTEGER,
      release: entry.release_date ?? "",
    };
  }
  return index;
}

/** 模型清单下拉比较：厂商块（目录序）→ 厂商内发布日期倒序 → 名称；索引里没有的
 *  模型（目录外自定义/站点自有命名）垫底按名称。 */
export function modelOrderCompare(index: Record<string, ModelOrderEntry>) {
  return (a: { key: string; name: string }, b: { key: string; name: string }) =>
    (index[a.key]?.vendorRank ?? Number.MAX_SAFE_INTEGER) - (index[b.key]?.vendorRank ?? Number.MAX_SAFE_INTEGER) ||
    (index[b.key]?.release ?? "").localeCompare(index[a.key]?.release ?? "") ||
    a.name.localeCompare(b.name);
}
