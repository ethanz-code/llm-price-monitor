import type { RankingsData } from "./types";

/** 模型名 → 榜单匹配键：与后端 rankings_key 同口径（小写，去空格/横线/下划线/点号）。
 *  AA slug 用连字符表达版本点号（gpt-6-5-sol），目录 id 常带点号（gpt-6.5-sol），归一后互认。 */
export function rankingsKey(model: string): string {
  return model.toLowerCase().replace(/[\s_.-]+/g, "");
}

/** 榜单条目的精简引用：目录与总览页并入展示用，避免把整份 269 行榜单传给表格组件。 */
export interface RankingHit {
  rank: number;
  intelligence_index: number | null;
  slug: string;
}

/** 榜单数据 → 匹配索引：slug 与展示名都参与键，同键多变体取排名最靠前的。 */
export function buildRankingIndex(data: RankingsData | null | undefined): Record<string, RankingHit> {
  const index: Record<string, RankingHit> = {};
  for (const entry of data?.models ?? []) {
    const hit: RankingHit = { rank: entry.rank, intelligence_index: entry.intelligence_index, slug: entry.slug };
    for (const key of [rankingsKey(entry.slug), rankingsKey(entry.name)]) {
      if (!key) continue;
      const existing = index[key];
      if (!existing || hit.rank < existing.rank) index[key] = hit;
    }
  }
  return index;
}

/** 模型 id → 榜单命中；对不上返回 null，不做模糊硬凑。 */
export function rankingHit(index: Record<string, RankingHit>, model: string): RankingHit | null {
  return index[rankingsKey(model)] ?? null;
}
