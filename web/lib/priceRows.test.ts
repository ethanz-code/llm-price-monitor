/** 首页精选行口径：每模型取全站综合价最低行、无价模型不出现、发布日期倒序排。 */
import { describe, expect, it } from "vitest";

import type { OverviewRecord } from "@/lib/types";
import { canonicalModel, lowestPriceRowPerModel, orderByReleaseDesc } from "@/lib/priceRows";

function row(over: Partial<OverviewRecord>): OverviewRecord {
  return {
    site_id: "s1",
    model: "demo",
    input_price: null,
    output_price: null,
    unit: "USD/1M tokens",
    price_status: "confirmed",
    requires_auth: false,
    source_url: "https://s1.example",
    captured_at: 100,
    fingerprint: "f",
    discount: null,
    ...over,
  };
}

describe("canonicalModel", () => {
  it("小写去分隔符合并同模型异写，点号保留", () => {
    expect(canonicalModel("GPT-5.6 Sol")).toBe("gpt5.6sol");
    expect(canonicalModel("gpt_5.6 sol")).toBe("gpt5.6sol");
    expect(canonicalModel("GLM-5.2")).not.toBe(canonicalModel("GLM52")); // 两个目录键不合并
  });
});

describe("lowestPriceRowPerModel", () => {
  it("每模型只留综合价最低的行（输入 3:输出 1 折 RMB），展示名取组内出现最多的写法", () => {
    const rate = 7.0;
    const rows = [
      row({ site_id: "a", model: "GPT-5.6", input_price: 10, output_price: 1 }),
      row({ site_id: "b", model: "gpt-5.6", input_price: 2, output_price: 8 }), // 综合价 (2*3+8)/4=3.5 更低
      row({ site_id: "c", model: "GPT-5.6", input_price: 30, output_price: 40 }),
    ];
    const picked = lowestPriceRowPerModel(rows, rate);
    expect(picked).toHaveLength(1);
    expect(picked[0].site_id).toBe("b");
    expect(picked[0].model).toBe("GPT-5.6"); // 组内出现最多的写法
  });

  it("全组无可用价的模型不出现；需认证行不算可用价", () => {
    const rows = [
      row({ model: "free-only", input_price: null, output_price: null, price_status: "auth_required" }),
      row({ model: "priced", input_price: 1, output_price: 1 }),
    ];
    const picked = lowestPriceRowPerModel(rows, null);
    expect(picked.map((r) => canonicalModel(r.model))).toEqual(["priced"]);
  });
});

describe("orderByReleaseDesc", () => {
  it("目录发布日期倒序在前，未收录模型垫底按名称", () => {
    const rows = [
      row({ model: "old-model" }),
      row({ model: "new-model" }),
      row({ model: "mid-model" }),
    ];
    const release = { newmodel: "2026-09-01", midmodel: "2026-01-01" };
    const ordered = orderByReleaseDesc(rows, release);
    expect(ordered.map((r) => r.model)).toEqual(["new-model", "mid-model", "old-model"]);
  });
});
