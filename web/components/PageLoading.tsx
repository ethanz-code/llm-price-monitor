"use client";

import { Skeleton } from "antd";

/** 数据页通用加载骨架：页头 + 统计条 + 面板，形状与真实布局一致。 */
export function PageLoading() {
  return (
    <div className="page">
      <div style={{ padding: "8px 0 32px" }}>
        <Skeleton active paragraph={{ rows: 1, width: ["40%"] }} title={{ width: "28%" }} />
      </div>
      <div className="stat-grid" style={{ marginBottom: 32 }}>
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="stat-card">
            <Skeleton active paragraph={{ rows: 1, width: ["60%"] }} title={{ width: "50%" }} />
          </div>
        ))}
      </div>
      <div className="panel" style={{ padding: 24 }}>
        <Skeleton active paragraph={{ rows: 6 }} />
      </div>
    </div>
  );
}
