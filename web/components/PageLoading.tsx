"use client";

import { Skel } from "./ui";

/** 数据页通用加载骨架：页头 + 统计条（可选） + 面板，形状与真实布局一致。 */
export function PageLoading({ stats = true }: { stats?: boolean }) {
  return (
    <div className="page">
      <div style={{ display: "grid", gap: 10, padding: "8px 0 32px" }}>
        <Skel w={120} h={12} />
        <Skel w={280} h={30} />
        <Skel w="55%" h={13} />
      </div>
      {stats && (
        <div className="stat-grid" style={{ marginBottom: 20 }}>
          {[0, 1, 2, 3].map((index) => (
            <div key={index} className="stat-card" style={{ display: "grid", gap: 8 }}>
              <Skel w="50%" h={11} />
              <Skel w="35%" h={24} />
              <Skel w="60%" h={11} />
            </div>
          ))}
        </div>
      )}
      <div className="panel" style={{ padding: 24, display: "grid", gap: 10 }}>
        {[0, 1, 2, 3, 4, 5].map((index) => (
          <Skel key={index} w={`${95 - index * 8}%`} h={14} />
        ))}
      </div>
    </div>
  );
}

/** 首页 landing 加载骨架：hero 左右两栏 + 实时亮点（3 列），形状与真实布局一致。 */
export function LandingLoading() {
  return (
    <div className="page landing">
      <section className="hero">
        <div className="hero-main" style={{ display: "grid", gap: 16 }}>
          <div style={{ display: "grid", gap: 8 }}>
            <Skel w="82%" h={52} />
            <Skel w="58%" h={52} />
          </div>
          <div style={{ display: "grid", gap: 8, marginTop: 6 }}>
            <Skel w="94%" h={13} />
            <Skel w="88%" h={13} />
          </div>
          <div style={{ display: "flex", gap: 12, marginTop: 8 }}>
            <Skel w={150} h={44} />
            <Skel w={128} h={44} />
          </div>
        </div>
        <aside className="hero-side">
          <div className="panel" style={{ padding: 18, minHeight: 140, display: "grid", gap: 8, alignContent: "end" }}>
            <Skel w="100%" h={3} style={{ borderRadius: 0 }} />
            <Skel w="100%" h={3} style={{ borderRadius: 0 }} />
            <Skel w="100%" h={3} style={{ borderRadius: 0 }} />
            <Skel w="100%" h={3} style={{ borderRadius: 0 }} />
          </div>
          <Skel w={64} h={12} />
          {[0, 1].map((index) => (
            <div key={index} style={{ display: "grid", gap: 6 }}>
              <Skel w={`${72 - index * 14}%`} h={12} />
              <Skel w={`${56 - index * 12}%`} h={11} />
            </div>
          ))}
        </aside>
      </section>
      <section className="landing-section">
        <div className="landing-section-head">
          <Skel w={110} h={22} />
        </div>
        <div className="landing-stats">
          {[0, 1, 2].map((index) => (
            <div key={index} className="landing-stat" style={{ display: "grid", gap: 8, alignContent: "start" }}>
              <Skel w="45%" h={11} />
              <Skel w="62%" h={26} />
              <Skel w="70%" h={11} />
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
