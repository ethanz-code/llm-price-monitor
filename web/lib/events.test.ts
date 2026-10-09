/** 事件折叠口径：首页侧栏与事件追踪页共用，同站同模型同类 120s 内折一张卡、公告不折叠。 */
import { describe, expect, it } from "vitest";

import { foldEvents } from "@/lib/events";
import type { EventRow, NoticeEvent } from "@/lib/types";

function priceEvent(over: Partial<EventRow>): EventRow {
  return { site_id: "s1", model: "m1", kind: "changed", detected_at: 1000, ...over };
}

function notice(over: Partial<NoticeEvent>): NoticeEvent {
  return { site_id: "s1", kind: "notice_changed", detected_at: 1000, content: "公告", ...over };
}

describe("foldEvents", () => {
  it("同站同模型同类 120s 内相邻事件折成一组", () => {
    const groups = foldEvents([
      priceEvent({ detected_at: 1000 }),
      priceEvent({ detected_at: 1100 }),
      priceEvent({ detected_at: 1180 }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0]).toHaveLength(3);
  });

  it("跨 120s、不同模型/站点/类型不折叠；公告事件永不折叠", () => {
    const groups = foldEvents([
      priceEvent({ detected_at: 1000 }),
      priceEvent({ detected_at: 1201 }), // 超窗
      priceEvent({ model: "m2", detected_at: 1202 }),
      priceEvent({ site_id: "s2", detected_at: 1203 }),
      priceEvent({ kind: "recovered", detected_at: 1204 }),
      notice({ detected_at: 1205 }),
      notice({ detected_at: 1206, site_id: "s2" }),
    ]);
    expect(groups).toHaveLength(7);
  });
});
