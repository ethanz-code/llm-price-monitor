"use client";

import { Skel } from "./ui";

/* 数据页通用骨架。形状对齐当前真实布局：数据页没有大页头，顶部是 PageDigest 单行摘要条
 * （细线收底），内容区按页型分表格 / 事件卡 / 站点检测面板三种；行高对齐 dtable 的
 * 默认密度（td 上下 10px ≈ 40px 行）与 dense 密度（上下 8px ≈ 36px 行）。 */

/** 页顶摘要条骨架：label+value 成对，复用 .page-digest 获得同样的底线与间距。 */
function DigestSkeleton({ count }: { count: number }) {
  return (
    <div className="page-digest" aria-hidden>
      {Array.from({ length: count }, (_, index) => (
        <span
          key={index}
          className="page-digest-item"
          style={{ display: "flex", alignItems: "baseline", gap: 8 }}
        >
          <Skel w={52} h={11} />
          <Skel w={64} h={18} />
        </span>
      ))}
    </div>
  );
}

/** 数据表骨架：列模板对应真实表（名称列自适应 + 右侧定宽数值列），行间发丝线。 */
const TABLE_COLS = "minmax(0, 1.6fr) repeat(3, 110px) 90px";
const TABLE_ROW_WIDTHS = [
  [0.72, 0.6, 0.55, 0.65, 0.7],
  [0.58, 0.5, 0.7, 0.5, 0.55],
  [0.8, 0.65, 0.45, 0.6, 0.6],
  [0.52, 0.45, 0.6, 0.55, 0.5],
];

function TableSkeleton({ rows = 8, dense = false }: { rows?: number; dense?: boolean }) {
  const rowPadding = dense ? "11px 12px" : "13px 12px";
  return (
    <div className="panel" style={{ overflow: "hidden" }} aria-hidden>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: TABLE_COLS,
          columnGap: 14,
          padding: "10px 12px",
          borderBottom: "1px solid var(--border)",
        }}
      >
        {[0.5, 0.8, 0.7, 0.8, 0.6].map((width, index) => (
          <Skel key={index} w={`${width * 100}%`} h={11} />
        ))}
      </div>
      {Array.from({ length: rows }, (_, index) => {
        const widths = TABLE_ROW_WIDTHS[index % TABLE_ROW_WIDTHS.length];
        return (
          <div
            key={index}
            style={{
              display: "grid",
              gridTemplateColumns: TABLE_COLS,
              columnGap: 14,
              padding: rowPadding,
              borderBottom: index < rows - 1 ? "1px solid var(--border)" : undefined,
            }}
          >
            {widths.map((width, column) => (
              <Skel key={column} w={`${width * 100}%`} h={13} />
            ))}
          </div>
        );
      })}
    </div>
  );
}

/** 事件流骨架：一行「事件 + 站点筛选」工具行 + event-card 列表（卡片复用真实容器样式）。 */
function CardsSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div style={{ display: "grid", gap: 24 }} aria-hidden>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
        <Skel w={40} h={15} />
        <Skel w={132} h={32} style={{ borderRadius: 8 }} />
      </div>
      <div style={{ display: "grid", gap: 14 }}>
        {Array.from({ length: rows }, (_, index) => (
          <div key={index} className="event-card">
            <Skel w={8} h={8} style={{ borderRadius: "50%", flexShrink: 0, marginTop: 4 }} />
            <div style={{ display: "grid", gap: 7, flex: 1 }}>
              <Skel w={`${58 - (index % 3) * 7}%`} h={13} />
              <Skel w={`${76 - (index % 4) * 9}%`} h={11} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** 站点检测详情骨架：页头（标题 + 分享按钮）+ 检测面板（时段色块行 + 图表区）+ 渠道列表。 */
function StatusSkeleton() {
  return (
    <>
      <div className="page-header" aria-hidden>
        <div
          style={{
            display: "flex",
            alignItems: "flex-end",
            justifyContent: "space-between",
            flexWrap: "wrap",
            gap: 24,
          }}
        >
          <Skel w={260} h={28} />
          <Skel w={110} h={30} style={{ borderRadius: 8 }} />
        </div>
      </div>
      <div style={{ display: "grid", gap: 16 }} aria-hidden>
        <section className="panel" style={{ padding: 16, display: "grid", gap: 14 }}>
          <div style={{ display: "flex", gap: 3 }}>
            {Array.from({ length: 28 }, (_, index) => (
              <Skel key={index} w="100%" h={22} style={{ flex: 1, borderRadius: 3 }} />
            ))}
          </div>
          <Skel w="100%" h={200} style={{ borderRadius: 8 }} />
        </section>
        <section className="panel" style={{ padding: 16, display: "grid", gap: 2 }}>
          <Skel w={96} h={16} style={{ marginBottom: 6 }} />
          {Array.from({ length: 5 }, (_, index) => (
            <div
              key={index}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "12px 0",
                borderBottom: index < 4 ? "1px solid var(--border)" : undefined,
              }}
            >
              <Skel w={130} h={13} />
              <Skel w={48} h={18} />
              <div style={{ marginLeft: "auto", display: "flex", gap: 14 }}>
                <Skel w={72} h={11} />
                <Skel w={64} h={11} />
                <Skel w={56} h={11} />
              </div>
            </div>
          ))}
        </section>
      </div>
    </>
  );
}

/** 数据页通用加载骨架：顶部摘要条（可选）+ 工具行（可选）+ 内容区，形状与真实布局一致。 */
export function PageLoading({
  digest = 0,
  toolbar = false,
  variant = "table",
  dense = false,
}: {
  /** 摘要条项数，0 不画 */
  digest?: number;
  /** 内容区上方的切换/搜索工具行（目录页） */
  toolbar?: boolean;
  /** 内容区形状：数据表 / 事件卡 / 站点检测面板 */
  variant?: "table" | "cards" | "panels";
  /** 表格紧凑密度（目录页两视图在用） */
  dense?: boolean;
}) {
  return (
    <div className="page">
      {digest > 0 && <DigestSkeleton count={digest} />}
      {toolbar && (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 12,
            alignItems: "center",
            marginBottom: 16,
          }}
          aria-hidden
        >
          <div style={{ display: "flex", gap: 6 }}>
            <Skel w={84} h={32} style={{ borderRadius: 8 }} />
            <Skel w={84} h={32} style={{ borderRadius: 8 }} />
          </div>
          <Skel w={260} h={32} style={{ borderRadius: 8 }} />
        </div>
      )}
      {variant === "table" && <TableSkeleton dense={dense} />}
      {variant === "cards" && <CardsSkeleton />}
      {variant === "panels" && <StatusSkeleton />}
    </div>
  );
}

/** 首页 landing 加载骨架：hero（左球 + 右文案 + 两排节点轮播）+ 简介 + 最新价表 + 趋势/事件两栏，
 *  形状与真实布局一致。 */
const PANO_WIDTHS = [
  [76, 96, 64, 88, 72, 104],
  [84, 60, 92, 68, 100, 76],
];

export function LandingLoading() {
  return (
    <div className="page landing">
      <section className="hero">
        <div className="hero-globe" aria-hidden>
          <div className="pano-globe-layer">
            <Skel
              w="76%"
              h={100}
              style={{
                height: "auto",
                aspectRatio: "1",
                borderRadius: "50%",
                margin: "10% auto 0",
                display: "block",
              }}
            />
          </div>
        </div>
        <div className="hero-main" aria-hidden>
          <div style={{ display: "grid", gap: 8 }}>
            <Skel w="82%" h={52} />
            <Skel w="58%" h={52} />
          </div>
          <div style={{ display: "grid", gap: 8, marginTop: 22 }}>
            <Skel w="94%" h={13} />
            <Skel w="72%" h={13} />
          </div>
          <div style={{ display: "flex", gap: 12, marginTop: 30 }}>
            <Skel w={150} h={44} style={{ borderRadius: 10 }} />
            <Skel w={128} h={44} style={{ borderRadius: 10 }} />
          </div>
        </div>
        {PANO_WIDTHS.map((widths, row) => (
          <div key={row} className="marquee-row" aria-hidden>
            <div
              className="marquee-track"
              style={{ width: "100%", justifyContent: "space-between", gap: 16 }}
            >
              {widths.map((width, index) => (
                <div
                  key={index}
                  style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 8px" }}
                >
                  <Skel w={8} h={8} style={{ borderRadius: "50%", flexShrink: 0 }} />
                  <Skel w={width} h={12} />
                  <Skel w={34} h={12} />
                </div>
              ))}
            </div>
          </div>
        ))}
      </section>

      <section className="landing-intro" aria-hidden>
        <Skel w="100%" h={13} />
        <Skel w="64%" h={13} style={{ marginInline: "auto" }} />
      </section>

      <section className="landing-section" aria-hidden>
        <div className="landing-section-head">
          <Skel w={132} h={22} />
          <Skel w={64} h={13} />
        </div>
        <div style={{ marginTop: 14 }}>
          <Skel w="42%" h={11} />
        </div>
        <div style={{ marginTop: 16 }}>
          <TableSkeleton rows={6} dense />
        </div>
      </section>

      <section className="landing-section landing-duo" aria-hidden>
        <div>
          <div className="landing-section-head">
            <Skel w={110} h={22} />
          </div>
          <div className="panel" style={{ marginTop: 20, padding: 16 }}>
            <Skel w="100%" h={210} style={{ borderRadius: 8 }} />
          </div>
        </div>
        <div>
          <div className="landing-section-head">
            <Skel w={96} h={22} />
            <Skel w={70} h={13} />
          </div>
          <div className="landing-events" style={{ marginTop: 20 }}>
            {[0, 1, 2].map((index) => (
              <div key={index} className="side-note">
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <Skel w={6} h={6} style={{ borderRadius: "50%", flexShrink: 0 }} />
                  <Skel w={`${52 - index * 6}%`} h={13} />
                </div>
                <Skel w={`${70 - index * 8}%`} h={11} style={{ marginTop: 6 }} />
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}
