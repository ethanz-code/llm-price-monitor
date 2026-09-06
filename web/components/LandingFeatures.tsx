"use client";

import { AimOutlined, FileSearchOutlined, NodeIndexOutlined } from "@ant-design/icons";

/** Landing 方法论特性卡：图标依赖 @ant-design/icons，需在客户端渲染。 */
const FEATURES = [
  {
    icon: <AimOutlined />,
    title: "直接请求取证",
    text: "按配置逐站点请求价格接口，每条价格都来自实际返回的 HTTP JSON 响应——不猜测、不插值、不搬运二手数据。",
  },
  {
    icon: <FileSearchOutlined />,
    title: "官方价锚定",
    text: "检索厂商官方定价页并提取原价，站点价与官方价相除得到折扣，每一条都能溯源到定价页出处。",
  },
  {
    icon: <NodeIndexOutlined />,
    title: "变化事件流",
    text: "每次采集与上一轮对比，新增、涨价、降价、恢复、状态变化实时进入事件流，历史曲线按天沉淀。",
  },
];

export function LandingFeatures() {
  return (
    <div className="feature-grid">
      {FEATURES.map((feature) => (
        <div key={feature.title} className="feature-card">
          <span className="feature-icon" aria-hidden>
            {feature.icon}
          </span>
          <h3>{feature.title}</h3>
          <p>{feature.text}</p>
        </div>
      ))}
    </div>
  );
}
