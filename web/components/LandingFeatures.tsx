"use client";

import { IconAim, IconFileSearch, IconNodes } from "./icons";

/** Landing 方法论：编辑感编号列表（非卡片），发丝线分行。 */
const FEATURES = [
  {
    icon: <IconAim size={16} />,
    title: "直接请求取证",
    text: "按配置逐站点请求价格接口，每条价格都能对应一段实际返回的 HTTP JSON 响应。",
  },
  {
    icon: <IconFileSearch size={16} />,
    title: "官方价锚定",
    text: "检索厂商官方定价页并提取原价，站点价与官方价相除得到折扣，每一条都能溯源到定价页出处。",
  },
  {
    icon: <IconNodes size={16} />,
    title: "变化事件流",
    text: "每次采集与上一轮对比，新增、涨价、降价、恢复、状态变化进入事件流，价格按天积累成历史曲线。",
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
