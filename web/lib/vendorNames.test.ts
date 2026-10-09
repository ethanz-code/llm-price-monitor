/** 厂商显示名映射：目录厂商名 → 用户熟悉的品牌名，未收录回落原始名。 */
import { describe, expect, it } from "vitest";

import { vendorDisplay } from "@/lib/vendorNames";

describe("vendorDisplay", () => {
  it("已收录厂商映射为中文品牌名", () => {
    expect(vendorDisplay("Alibaba Cloud")).toBe("通义千问 Qwen");
    expect(vendorDisplay("Zhipu AI")).toBe("智谱 GLM");
    expect(vendorDisplay("Moonshot AI")).toBe("月之暗面 Kimi");
    expect(vendorDisplay("Volcengine Ark")).toBe("火山方舟 豆包");
  });

  it("大小写与首尾空格不影响映射", () => {
    expect(vendorDisplay(" alibaba cloud ")).toBe("通义千问 Qwen");
  });

  it("未收录厂商回落原始名", () => {
    expect(vendorDisplay("OpenAI")).toBe("OpenAI");
    expect(vendorDisplay("DeepSeek")).toBe("DeepSeek");
    expect(vendorDisplay("Deep Infra")).toBe("Deep Infra");
  });
});
