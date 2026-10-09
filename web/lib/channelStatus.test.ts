/** 非对话渠道过滤：生图/生视频渠道不并入状态列表，先钉住。 */
import { describe, expect, it } from "vitest";

import { channelDotsBySite } from "@/lib/channelStatus";

/** 造一条状态记录：items 里的渠道条目带 name + status。 */
function record(siteId: string, items: { name: string; status: string }[]) {
  return {
    site_id: siteId,
    captured_at: 1760000000,
    data: { items },
  };
}

describe("channelDotsBySite 非对话渠道过滤", () => {
  it("生图渠道（图像/生图/image 家族）不进列表", () => {
    const rows = channelDotsBySite([
      record("demo", [
        { name: "claude 满血", status: "up" },
        { name: "生图", status: "up" },
        { name: "grok生图", status: "down" },
        { name: "香蕉4k生图", status: "up" },
        { name: "image-4k", status: "up" },
        { name: "ImageGen", status: "up" },
        { name: "绘图增强", status: "up" },
      ]),
    ])["demo"];
    expect(rows.map((row) => row.name)).toEqual(["claude 满血"]);
  });

  it("生视频渠道：通用词（视频）与模型名（veo/sora/seedence）都滤掉", () => {
    const rows = channelDotsBySite([
      record("demo", [
        { name: "gemini视频", status: "up" },
        { name: "grok视频", status: "up" },
        { name: "veo3.1", status: "up" },
        { name: "sora-v3-pro", status: "down" },
        { name: "seedence官key", status: "up" },
        { name: "专用gemini", status: "up" },
        { name: "企业gpt（不降智）", status: "up" },
      ]),
    ])["demo"];
    expect(rows.map((row) => row.name)).toEqual(["专用gemini", "企业gpt（不降智）"]);
  });

  it("过宽词根不误伤文本模型（seed/wan 词根不在名单里）", () => {
    const rows = channelDotsBySite([
      record("demo", [
        { name: "doubao-seed-1.6", status: "up" },
        { name: "gpt-5", status: "up" },
      ]),
    ])["demo"];
    expect(rows.map((row) => row.name)).toEqual(["doubao-seed-1.6", "gpt-5"]);
  });
});
