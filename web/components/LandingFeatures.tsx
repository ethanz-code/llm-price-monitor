"use client";

import { IconAim, IconFileSearch, IconMonitor, IconNodes } from "./icons";

/** 首页能力速览：两列编号列表，每条一句话，讲清楚能查到什么、去哪页看。 */
const FEATURES = [
  {
    icon: <IconAim size={16} />,
    title: "直接抄价，不靠猜",
    text: "站点标多少就记多少，每条价格都附来源链接，点开就能核对。",
  },
  {
    icon: <IconFileSearch size={16} />,
    title: "厂商价对照，折扣现形",
    text: "折扣按厂商原价算，不认站点自报；各家厂商价收在「厂商定价」页。",
  },
  {
    icon: <IconNodes size={16} />,
    title: "变化全记下来",
    text: "涨价、降价、下架、恢复，都记进「历史与事件」，什么时候变的翻得到。",
  },
  {
    icon: <IconMonitor size={16} />,
    title: "渠道状态与公告",
    text: "渠道可用率、响应延迟，站点发的维护、跑路公告，也一并盯着。",
  },
];

export function LandingFeatures() {
  return (
    <div className="method-list">
      {FEATURES.map((feature, index) => (
        <div key={feature.title} className="method-item">
          <span className="method-num mono">{String(index + 1).padStart(2, "0")}</span>
          <div className="method-body">
            <h3>
              <span className="method-icon" aria-hidden>{feature.icon}</span>
              {feature.title}
            </h3>
            <p>{feature.text}</p>
          </div>
        </div>
      ))}
    </div>
  );
}
