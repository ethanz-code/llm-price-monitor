"use client";

import { Skel } from "./ui";

/** 数据页通用加载骨架：页头 + 统计条 + 面板，形状与真实布局一致。 */
export function PageLoading() {
  return (
    <div className="page">
      <div style={{ display: "grid", gap: 10, padding: "8px 0 32px" }}>
        <Skel w={120} h={12} />
        <Skel w={280} h={30} />
        <Skel w="55%" h={13} />
      </div>
      <div className="stat-grid" style={{ marginBottom: 32 }}>
        {[0, 1, 2, 3].map((index) => (
          <div key={index} className="stat-card" style={{ display: "grid", gap: 8 }}>
            <Skel w="50%" h={11} />
            <Skel w="35%" h={24} />
            <Skel w="60%" h={11} />
          </div>
        ))}
      </div>
      <div className="panel" style={{ padding: 24, display: "grid", gap: 10 }}>
        {[0, 1, 2, 3, 4, 5].map((index) => (
          <Skel key={index} w={`${95 - index * 8}%`} h={14} />
        ))}
      </div>
    </div>
  );
}
