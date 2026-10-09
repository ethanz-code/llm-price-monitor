/** 模型清单下拉的目录口径排序：厂商分块（目录序）→ 厂商内发布倒序 → 名称，目录外垫底。 */
import { describe, expect, it } from "vitest";

import { canonicalModel, catalogModelOrderIndex, modelOrderCompare } from "@/lib/modelOrder";
import type { CatalogEntry } from "@/lib/types";

function entry(model: string, vendor: string, release: string | null): CatalogEntry {
  return { found: true, model, vendor, currency: "USD", release_date: release };
}

const catalog: Record<string, CatalogEntry> = {
  m1: entry("gpt-6", "OpenAI", "2026-09-01"),
  m2: entry("glm-5.3", "Zhipu AI", "2026-09-18"),
  m3: entry("GPT_5.5", "OpenAI", "2026-08-01"),
  m4: entry("glm-5", "Zhipu AI", null),
};

const item = (model: string) => ({ key: canonicalModel(model), name: model });

describe("catalogModelOrderIndex", () => {
  it("键为模型归一键，厂商序号按目录里厂商首次出现顺序", () => {
    const index = catalogModelOrderIndex(catalog);
    expect(index.gpt6).toEqual({ vendorRank: 0, release: "2026-09-01" });
    expect(index["gpt5.5"].vendorRank).toBe(0);
    expect(index["glm5.3"].vendorRank).toBe(1);
    expect(index.glm5.release).toBe("");
  });
});

describe("modelOrderCompare", () => {
  it("厂商块按目录序，厂商内发布倒序，目录外垫底按名称", () => {
    const compare = modelOrderCompare(catalogModelOrderIndex(catalog));
    const ordered = [item("自定义-model"), item("glm-5"), item("glm-5.3"), item("GPT_5.5"), item("gpt-6")];
    expect([...ordered].sort((a, b) => compare(a, b)).map((x) => x.name)).toEqual([
      "gpt-6",
      "GPT_5.5",
      "glm-5.3",
      "glm-5",
      "自定义-model",
    ]);
  });

  it("没传索引时全部垫底，退化为纯名称序", () => {
    const compare = modelOrderCompare({});
    const ordered = [item("b"), item("a")];
    expect([...ordered].sort((a, b) => compare(a, b)).map((x) => x.name)).toEqual(["a", "b"]);
  });
});
