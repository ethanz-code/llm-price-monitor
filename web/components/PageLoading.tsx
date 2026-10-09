/* 数据页骨架屏：逐页对齐真实首屏布局。摘要条复用 .page-digest，卡片复用 .event-card，
 * 分页条复用 .pager，列模板与面板头取自各真实组件（DataTable 调用方的列定义、
 * ModelPicker、HistoryView、状态页 ch-row、Calculator、SnapshotPreview），
 * 内容换入时只剩「灰条 → 文字」的原位变化，不再跳版。
 * 所有条块带负相位错峰（Skel delay），整页 shimmer 呈波浪扫过而非同步闪。 */

import { LoadingRows, Skel } from "./ui";

/* ---------- 通用零件 ---------- */

/** 摘要条骨架：label+value 成对，复用 .page-digest 获得同样的底线与间距。 */
function DigestSkeleton({ labels }: { labels: string[] }) {
  return (
    <div className="page-digest" aria-hidden>
      {labels.map((label, index) => (
        <span
          key={label}
          className="page-digest-item"
          style={{ display: "flex", alignItems: "baseline", gap: 8 }}
        >
          <Skel w={label.length * 13} h={12} delay={index * 90} />
          <Skel w={56} h={26} delay={index * 90 + 45} />
        </span>
      ))}
    </div>
  );
}

type Col = number | string;

const colsTemplate = (cols: Col[]) =>
  cols.map((col) => (typeof col === "number" ? `${col}px` : col)).join(" ");

/** 单元格条宽：行×列决定 52%–80% 的伪随机宽度，避免整列齐刷刷。 */
const cellWidth = (row: number, col: number) => `${52 + ((row * 7 + col * 13) % 5) * 7}%`;

/** 分页条骨架：复用 .pager 获得真实分页的布局盒。 */
function PagerSkeleton() {
  return (
    <div className="pager" aria-hidden>
      <span className="pager-info">
        <Skel w={64} h={12} />
      </span>
      <Skel w={58} h={28} style={{ borderRadius: 6 }} />
      {[0, 1, 2, 3, 4].map((index) => (
        <Skel key={index} w={28} h={28} style={{ borderRadius: 6 }} delay={index * 60} />
      ))}
      <Skel w={58} h={28} style={{ borderRadius: 6 }} />
      <Skel w={104} h={12} />
    </div>
  );
}

/** 数据表骨架：列模板对应真实表；可选面板头栏（总览页）与分页条。 */
function PanelTable({
  cols,
  rows = 12,
  dense = false,
  head = false,
  pager = false,
  /** 行内容两行高（点阵/双行单元格的表）：加大行留白对齐真实行高 */
  tallRows = false,
}: {
  cols: Col[];
  rows?: number;
  dense?: boolean;
  /** 总览页面板头栏：模型名 + AA 徽标 | 数据时间 · 汇率 */
  head?: boolean;
  pager?: boolean;
  tallRows?: boolean;
}) {
  return (
    <div className="panel" style={{ overflow: "hidden" }} aria-hidden>
      {head && (
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 8,
            padding: "14px 20px",
            borderBottom: "1px solid var(--border)",
          }}
        >
          <Skel w={150} h={15} />
          <Skel w={226} h={13} delay={90} />
        </div>
      )}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: colsTemplate(cols),
          columnGap: 12,
          padding: dense ? "8px 12px" : "10px 12px",
          borderBottom: "1px solid var(--border)",
        }}
      >
        {cols.map((_, index) => (
          <Skel key={index} w="58%" h={11} delay={index * 40} />
        ))}
      </div>
      {Array.from({ length: rows }, (_, row) => (
        <div
          key={row}
          style={{
            display: "grid",
            gridTemplateColumns: colsTemplate(cols),
            columnGap: 12,
            padding: `${tallRows ? 24 : dense ? 10 : 12}px 12px`,
            borderBottom: row < rows - 1 ? "1px solid var(--border)" : undefined,
          }}
        >
          {cols.map((_, col) => (
            <Skel key={col} w={cellWidth(row, col)} h={13} delay={row * 130 + col * 47} />
          ))}
        </div>
      ))}
      {pager && <PagerSkeleton />}
    </div>
  );
}

/* ---------- 各页骨架（被对应路由的 loading.tsx 引用） ---------- */

/** 总览页：摘要条 3 项 + 模型选择下拉（300px）+ 面板头栏 + 8 列表（对齐 OverviewTable）。 */
export function OverviewLoading() {
  return (
    <div className="page">
      <DigestSkeleton labels={["站点", "模型", "价格记录"]} />
      <div style={{ marginBottom: 16 }} aria-hidden>
        <Skel w={300} h={34} style={{ borderRadius: 6, maxWidth: "100%" }} />
      </div>
      <PanelTable
        cols={["minmax(0,1.1fr)", 120, 120, 120, 110, 150, 200, "minmax(0,1.1fr)"]}
        dense
        head
        pager
        tallRows
        rows={18}
      />
    </div>
  );
}

/** 榜单页：摘要条 2 项 + 搜索行（搜索框 + 数据时间文案）+ 7 列表（对齐 RankingsTable）。 */
export function RankingsLoading() {
  return (
    <div className="page">
      <DigestSkeleton labels={["上榜模型", "评测厂商"]} />
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
        <Skel
          w={420}
          h={34}
          style={{ borderRadius: 6, flex: "1 1 260px", maxWidth: "min(420px, 100%)" }}
        />
        <Skel w={186} h={12} delay={120} />
      </div>
      <PanelTable
        cols={["minmax(72px,auto)", "minmax(220px,1.5fr)", 130, 84, 96, 100, 92]}
        dense
        pager
        rows={40}
      />
    </div>
  );
}

/** 目录页：摘要条 2 项 + 双视图切换行（Seg + 搜索 + 厂商下拉 + 榜单链接）
 *  + 6 列表（对齐 CatalogTable 官方定价视图，默认视图）。 */
export function CatalogLoading() {
  return (
    <div className="page">
      <DigestSkeleton labels={["官方定价模型", "全量渠道模型"]} />
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
        <div
          style={{
            display: "flex",
            gap: 2,
            padding: 3,
            background: "var(--field-bg)",
            borderRadius: 6,
          }}
        >
          <Skel w={68} h={26} style={{ borderRadius: 4 }} />
          <Skel w={68} h={26} style={{ borderRadius: 4 }} delay={60} />
        </div>
        <Skel
          w={420}
          h={34}
          style={{ borderRadius: 6, flex: "1 1 260px", maxWidth: "min(420px, 100%)" }}
          delay={100}
        />
        <Skel w={160} h={34} style={{ borderRadius: 6, maxWidth: "100%" }} delay={160} />
        <Skel w={66} h={13} style={{ marginLeft: "auto" }} delay={200} />
      </div>
      <PanelTable cols={[170, 248, 190, 190, "minmax(0,1fr)", 60]} dense pager tallRows rows={28} />
    </div>
  );
}

/** 日期分节头骨架：日期 + 发丝线 + 条数，对齐 EventFeed 的分节结构。 */
function DayHeadSkeleton() {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12 }} aria-hidden>
      <Skel w={88} h={12} />
      <span style={{ flex: 1, height: 1, background: "var(--border)" }} />
      <Skel w={30} h={12} delay={60} />
    </div>
  );
}

/** 事件卡骨架：标签 + 站点 + 模型 + 时间一行、摘要一行，对齐真实 event-card 结构。 */
function EventCardSkeleton({ row }: { row: number }) {
  return (
    <div className="event-card" aria-hidden>
      <div style={{ display: "grid", gap: 7, flex: 1 }}>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <Skel w={46} h={22} style={{ borderRadius: 999 }} delay={row * 130} />
          <Skel w={92} h={13} delay={row * 130 + 40} />
          <Skel w={150} h={13} delay={row * 130 + 80} />
          <Skel w={96} h={12} style={{ marginLeft: "auto" }} delay={row * 130 + 110} />
        </div>
        <Skel w={`${64 - (row % 3) * 7}%`} h={13} delay={row * 130 + 150} />
      </div>
    </div>
  );
}

/** 历史页：摘要条 2 项 + 「事件 | 站点筛选」工具行 + 按天分节的事件卡（对齐 HistoryView）。 */
export function HistoryLoading() {
  return (
    <div className="page">
      <DigestSkeleton labels={["价格事件", "公告"]} />
      <div style={{ display: "grid", gap: 24 }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            flexWrap: "wrap",
            gap: 12,
            alignItems: "center",
          }}
          aria-hidden
        >
          <Skel w={36} h={15} />
          <Skel w={128} h={34} style={{ borderRadius: 6 }} delay={80} />
        </div>
        <div style={{ display: "grid", gap: 10 }}>
          <DayHeadSkeleton />
          {[0, 1, 2, 3].map((row) => (
            <EventCardSkeleton key={row} row={row} />
          ))}
        </div>
      </div>
    </div>
  );
}

/** 渠道状态行骨架：6 列（渠道/状态/延迟/成功率/可用记录/正常次数），对齐 .ch-row 列模板。 */
const CH_COLS = "minmax(110px,200px) auto auto 1fr minmax(120px,220px) auto";

function ChannelRowSkeleton({ row, head = false }: { row: number; head?: boolean }) {
  return (
    <div
      className={`ch-row${head ? " ch-head" : ""}`}
      style={{ padding: "10px 0", borderBottom: "1px solid var(--border)" }}
      aria-hidden
    >
      {head ? (
        <>
          <Skel w={44} h={12} />
          <Skel w={32} h={12} delay={20} />
          <Skel w={96} h={12} delay={40} />
          <Skel w={92} h={12} delay={60} />
          <Skel w={72} h={12} delay={80} />
          <Skel w={56} h={12} delay={100} />
        </>
      ) : (
        <>
          <div style={{ display: "grid", gap: 4 }}>
            <Skel w={110} h={13} delay={row * 120} />
            <Skel w={150} h={11} delay={row * 120 + 30} />
          </div>
          <Skel w={64} h={22} style={{ borderRadius: 999 }} delay={row * 120 + 50} />
          <Skel w={104} h={13} delay={row * 120 + 70} />
          <Skel w={128} h={13} delay={row * 120 + 90} />
          <div style={{ display: "flex", gap: 3 }}>
            {Array.from({ length: 15 }, (_, index) => (
              <Skel
                key={index}
                w={7}
                h={14}
                style={{ borderRadius: 2, flexShrink: 0 }}
                delay={row * 120 + index * 12}
              />
            ))}
          </div>
          <Skel w={48} h={13} delay={row * 120 + 110} />
        </>
      )}
    </div>
  );
}

/** 站点检测详情页：页头（标题 + 分享按钮）+ 检测面板（时段色块 + 两张图）
 *  + 渠道状态列表 + 站点公告（对齐 status/[siteId] 页）。 */
export function StatusLoading() {
  return (
    <div className="page">
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
          <Skel w={280} h={28} />
          <Skel
            w={110}
            h={34}
            style={{ borderRadius: 8, marginBottom: 4 }}
            delay={60}
          />
        </div>
      </div>
      <div style={{ display: "grid", gap: 16 }} aria-hidden>
        <section className="panel" style={{ display: "grid", gap: 14, padding: 16 }}>
          <div style={{ display: "flex", gap: 3 }}>
            {Array.from({ length: 28 }, (_, index) => (
              <Skel
                key={index}
                w="100%"
                h={22}
                style={{ flex: 1, borderRadius: 3 }}
                delay={index * 30}
              />
            ))}
          </div>
          <div style={{ paddingTop: 4, display: "grid", gap: 18 }}>
            <Skel w="100%" h={190} style={{ borderRadius: 8 }} delay={220} />
            <Skel w="100%" h={190} style={{ borderRadius: 8 }} delay={340} />
          </div>
        </section>
        <section className="panel" style={{ display: "grid", gap: 2, padding: 16 }}>
          <Skel w={110} h={16} style={{ marginBottom: 6 }} />
          <ChannelRowSkeleton row={0} head />
          {[0, 1, 2, 3, 4, 5].map((row) => (
            <ChannelRowSkeleton key={row} row={row} />
          ))}
        </section>
        <section className="panel" style={{ display: "grid", gap: 10, padding: 16 }}>
          <div style={{ display: "flex", gap: 12, alignItems: "baseline" }}>
            <Skel w={64} h={15} />
            <Skel w={170} h={12} delay={60} />
          </div>
          <Skel w="88%" h={13} delay={100} />
          <Skel w="72%" h={13} delay={160} />
          <Skel w="80%" h={13} delay={220} />
        </section>
        <Skel w={130} h={13} />
      </div>
    </div>
  );
}

/** 花费计算页：摘要条 2 项 + 模型选择块 + 价格/用量块 + 结果块（对齐 Calculator 三面板）。 */
export function CalcLoading() {
  return (
    <div className="page">
      <DigestSkeleton labels={["官方价模型", "站点"]} />
      <div style={{ display: "grid", gap: 16 }}>
        <section className="panel calc-block" aria-hidden>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              flexWrap: "wrap",
              marginBottom: 14,
            }}
          >
            <Skel w={88} h={15} />
            <Skel
              w={150}
              h={34}
              style={{ borderRadius: 6, marginLeft: "auto" }}
              delay={60}
            />
          </div>
          <Skel w="100%" h={34} style={{ borderRadius: 6 }} delay={100} />
          <div style={{ display: "grid", gap: 2, marginTop: 10 }}>
            {[0, 1, 2, 3, 4, 5].map((row) => (
              <div
                key={row}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: 12,
                  padding: "8px 10px",
                }}
              >
                <Skel w={`${46 - (row % 3) * 6}%`} h={13} delay={row * 90} />
                <Skel w={92} h={13} delay={row * 90 + 40} />
              </div>
            ))}
          </div>
        </section>
        <section className="panel calc-block" aria-hidden>
          <Skel w={96} h={15} style={{ marginBottom: 14 }} />
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
              gap: 12,
            }}
          >
            {[0, 1, 2, 3].map((index) => (
              <div key={index} style={{ display: "grid", gap: 6 }}>
                <Skel w={72} h={12} delay={index * 60} />
                <Skel
                  w="100%"
                  h={34}
                  style={{ borderRadius: 6 }}
                  delay={index * 60 + 20}
                />
              </div>
            ))}
          </div>
          <Skel w={72} h={15} style={{ margin: "18px 0 14px" }} />
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
              gap: 12,
            }}
          >
            {[0, 1].map((index) => (
              <div key={index} style={{ display: "grid", gap: 6 }}>
                <Skel w={84} h={12} delay={index * 60 + 240} />
                <Skel
                  w="100%"
                  h={34}
                  style={{ borderRadius: 6 }}
                  delay={index * 60 + 260}
                />
              </div>
            ))}
          </div>
          <Skel w={220} h={12} style={{ marginTop: 14 }} delay={380} />
        </section>
        <section className="panel calc-block" aria-hidden>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              flexWrap: "wrap",
              marginBottom: 14,
            }}
          >
            <Skel w={72} h={15} />
            <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
              <Skel w={96} h={28} style={{ borderRadius: 6 }} delay={60} />
              <Skel w={64} h={28} style={{ borderRadius: 6 }} delay={100} />
            </div>
          </div>
          <div
            style={{
              display: "flex",
              alignItems: "baseline",
              gap: 10,
              marginBottom: 16,
            }}
          >
            <Skel w={64} h={13} />
            <Skel w={140} h={26} delay={80} />
          </div>
          <PanelTable
            cols={["minmax(0,1.4fr)", 110, 110, 110, 90]}
            rows={4}
          />
        </section>
      </div>
    </div>
  );
}

/** 管理面板概览骨架：KPI 统计行 + 主栏（访问统计 / 采集报错 / 最近事件）+ 右栏快捷面板，
 *  对齐 /admin 概览的 dash-grid 布局。 */
export function AdminLoading() {
  return (
    <div className="page" aria-hidden>
      <h1 className="sr-only">控制台</h1>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "10px 40px", padding: "14px 20px 12px", background: "var(--panel)", borderRadius: 12 }}>
        {["检测站点", "价格记录", "事件总数", "输入折扣中位数"].map((label, index) => (
          <div key={label} style={{ display: "grid", gap: 6 }}>
            <Skel w={label.length * 12} h={12} delay={index * 70} />
            <Skel w={64} h={20} delay={index * 70 + 30} />
          </div>
        ))}
      </div>
      <div className="dash-grid" style={{ marginTop: 20 }}>
        <div className="dash-main">
          <div className="panel" style={{ padding: 16 }}>
            <Skel w={92} h={15} />
            <Skel w="100%" h={190} style={{ borderRadius: 8, marginTop: 14 }} delay={80} />
          </div>
          <div className="panel" style={{ padding: 16 }}>
            <Skel w={92} h={15} />
            <LoadingRows rows={4} style={{ marginTop: 14 }} />
          </div>
          <div className="panel" style={{ padding: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <Skel w={80} h={15} />
              <Skel w={64} h={12} delay={60} />
            </div>
            <div style={{ display: "grid", gap: 10, marginTop: 14 }}>
              {[0, 1, 2].map((row) => (
                <div key={row} className="event-card">
                  <Skel w={8} h={8} style={{ borderRadius: "50%", flexShrink: 0, marginTop: 4 }} delay={row * 120} />
                  <div style={{ display: "grid", gap: 6, flex: 1 }}>
                    <Skel w={`${56 - row * 7}%`} h={13} delay={row * 120 + 30} />
                    <Skel w={`${72 - row * 9}%`} h={11} delay={row * 120 + 60} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
        <aside className="dash-side" style={{ display: "grid", gap: 20 }}>
          <div className="panel" style={{ padding: 16, display: "grid", gap: 12 }}>
            <Skel w={76} h={15} />
            {[0, 1, 2, 3].map((row) => (
              <Skel key={row} w={`${70 - row * 6}%`} h={13} delay={row * 80} />
            ))}
          </div>
        </aside>
      </div>
    </div>
  );
}

/* ---------- 首页 landing 骨架 ---------- */

/** hero 节点轮播一排：点 + 站名 + 百分比，对齐 pano-site 胶囊。 */
const PANO_WIDTHS = [
  [76, 96, 64, 88, 72, 104],
  [84, 60, 92, 68, 100, 76],
];

/** 站点总览锚点卡骨架：标签 + 大数字 + 概况行，对齐 .site-card--overview。 */
function SiteOverviewSkeleton() {
  return (
    <div className="site-card site-card--wide site-card--overview" aria-hidden>
      <Skel w={56} h={12} />
      <Skel w={124} h={38} delay={60} />
      <Skel w={168} h={12} delay={120} />
    </div>
  );
}

/** 站点卡骨架：名称行 + 检测色点条 + 统计行 + 公告行，对齐 .site-card。 */
function SiteCardSkeleton({ index }: { index: number }) {
  return (
    <div className="site-card" aria-hidden>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <Skel
          w={8}
          h={8}
          style={{ borderRadius: "50%", flexShrink: 0 }}
          delay={index * 110}
        />
        <Skel w={90} h={13} delay={index * 110 + 20} />
        <Skel w={84} h={12} style={{ marginLeft: "auto" }} delay={index * 110 + 40} />
      </div>
      <div style={{ display: "flex", gap: 4, marginTop: 12 }}>
        {Array.from({ length: 15 }, (_, dot) => (
          <Skel
            key={dot}
            w={8}
            h={8}
            style={{ borderRadius: "50%", flexShrink: 0 }}
            delay={index * 110 + dot * 15}
          />
        ))}
      </div>
      <Skel w={150} h={12} style={{ marginTop: 10 }} delay={index * 110 + 60} />
      <Skel
        w={`${78 - (index % 3) * 9}%`}
        h={12}
        style={{ marginTop: 10 }}
        delay={index * 110 + 80}
      />
    </div>
  );
}

/** 榜单速览行骨架：#排名 / 模型 / 厂商 / 指数，对齐 .rank-row 四列模板。 */
const RANK_COLS = "2.4em minmax(0, 1fr) 160px 4.5em";

function RankListSkeleton() {
  return (
    <div className="rank-list" aria-hidden>
      <div
        className="rank-list-head"
        style={{ display: "grid", gridTemplateColumns: RANK_COLS, columnGap: 14 }}
      >
        <Skel w={16} h={11} />
        <Skel w={36} h={11} delay={20} />
        <Skel w={36} h={11} delay={40} />
        <Skel w={48} h={11} delay={60} />
      </div>
      {[0, 1, 2, 3, 4].map((row) => (
        <div
          key={row}
          className="rank-row"
          style={{
            display: "grid",
            gridTemplateColumns: RANK_COLS,
            columnGap: 14,
            alignItems: "center",
            borderBottom: "1px solid var(--border)",
            padding: "11px 10px",
          }}
        >
          <Skel w={30} h={13} delay={row * 120} />
          <Skel w={`${58 - (row % 3) * 8}%`} h={13} delay={row * 120 + 30} />
          <Skel w={100} h={13} delay={row * 120 + 60} />
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "flex-end",
              gap: 4,
            }}
          >
            <Skel w={34} h={13} delay={row * 120 + 90} />
            <Skel w={30} h={3} delay={row * 120 + 110} style={{ borderRadius: 2 }} />
          </div>
        </div>
      ))}
    </div>
  );
}

/** 首页骨架：hero（左球 + 右文案 + 两排节点轮播）+ 能力总览 + 采集流水线 + 使用场景
 *  + 最新价表 + 趋势/事件两栏 + 站点卡 + 榜单速览 + 数据来源 + FAQ + CTA，节次与真实页一致，换入不跳版。 */
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
            <Skel w="58%" h={52} delay={80} />
          </div>
          <div style={{ display: "grid", gap: 8, marginTop: 22 }}>
            <Skel w="94%" h={13} delay={160} />
            <Skel w="72%" h={13} delay={200} />
          </div>
          <div style={{ display: "flex", gap: 12, marginTop: 30 }}>
            <Skel w={150} h={44} style={{ borderRadius: 10 }} delay={260} />
            <Skel w={128} h={44} style={{ borderRadius: 10 }} delay={300} />
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
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "7px 8px",
                  }}
                >
                  <Skel
                    w={8}
                    h={8}
                    style={{ borderRadius: "50%", flexShrink: 0 }}
                    delay={row * 200 + index * 45}
                  />
                  <Skel w={width} h={12} delay={row * 200 + index * 45 + 15} />
                  <Skel w={34} h={12} delay={row * 200 + index * 45 + 30} />
                </div>
              ))}
            </div>
          </div>
        ))}
      </section>

      {/* 叙事三区骨架：能力总览（横滑图标卡列）→ 采集流水线（舞台横带）→ 使用场景（编号可点卡） */}
      <section className="landing-section landing-statement" aria-hidden>
        <div className="landing-section-head">
          <Skel w={230} h={36} />
        </div>
        <div style={{ marginTop: 14 }}>
          <Skel w="46%" h={12} delay={80} />
        </div>
        <div className="cap-row" style={{ marginTop: 24 }}>
          {[0, 1, 2, 3].map((row) => (
            <div key={row} className="cap-card">
              <Skel w={44} h={44} style={{ borderRadius: 10 }} delay={row * 70} />
              <Skel w={`${64 - (row % 2) * 8}%`} h={15} delay={row * 70 + 20} />
              <Skel w="88%" h={12} delay={row * 70 + 40} />
              <Skel w={56} h={20} style={{ borderRadius: 9999, marginTop: "auto" }} delay={row * 70 + 55} />
            </div>
          ))}
        </div>
      </section>

      <section className="landing-section landing-statement" aria-hidden>
        <div className="pipe-stage">
          <div className="landing-section-head">
            <Skel w={250} h={36} />
          </div>
          <div style={{ marginTop: 14, marginBottom: 30 }}>
            <Skel w="52%" h={12} delay={80} />
          </div>
          <div className="pipe-band">
            {[0, 1, 2, 3, 4, 5].map((step) => (
              <div key={step} className="pipe-step">
                <Skel w={30} h={30} style={{ borderRadius: 9999 }} delay={step * 60} />
                <Skel w={52} h={15} delay={step * 60 + 20} />
                <Skel w="90%" h={11} delay={step * 60 + 40} />
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="landing-section landing-statement" aria-hidden>
        <div className="landing-section-head">
          <Skel w={200} h={36} />
        </div>
        <div style={{ marginTop: 14 }}>
          <Skel w="40%" h={12} delay={80} />
        </div>
        <div className="use-grid" style={{ marginTop: 26 }}>
          {[0, 1, 2, 3].map((row) => (
            <div key={row} className="use-card">
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                <Skel w={22} h={13} delay={row * 70} />
                <Skel w={14} h={14} delay={row * 70 + 15} />
              </div>
              <Skel w={96} h={17} delay={row * 70 + 25} />
              <Skel w={`${76 - (row % 2) * 8}%`} h={12} delay={row * 70 + 35} />
            </div>
          ))}
        </div>
      </section>

      <section className="landing-section" aria-hidden>
        <div className="landing-section-head">
          <Skel w={132} h={22} />
          <Skel w={64} h={13} delay={60} />
        </div>
        <div style={{ marginTop: 14 }}>
          <Skel w="42%" h={11} delay={100} />
        </div>
        <div style={{ marginTop: 16 }}>
          <PanelTable
            cols={["minmax(100px,140px)", "minmax(0,1fr)", 130, 130, 140, 150]}
            rows={6}
          />
        </div>
      </section>

      <section className="landing-section landing-duo" aria-hidden>
        <div>
          <div className="landing-section-head">
            <Skel w={110} h={22} />
          </div>
          <div className="panel" style={{ marginTop: 20, padding: 16 }}>
            <Skel w="100%" h={210} style={{ borderRadius: 8 }} delay={120} />
          </div>
        </div>
        <div>
          <div className="landing-section-head">
            <Skel w={96} h={22} />
            <Skel w={70} h={13} delay={60} />
          </div>
          <div className="landing-events" style={{ marginTop: 20 }}>
            {[0, 1, 2].map((row) => (
              <div key={row} className="side-note">
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <Skel
                    w={6}
                    h={6}
                    style={{ borderRadius: "50%", flexShrink: 0 }}
                    delay={row * 130}
                  />
                  <Skel w={`${52 - row * 6}%`} h={13} delay={row * 130 + 30} />
                </div>
                <Skel
                  w={`${70 - row * 8}%`}
                  h={11}
                  style={{ marginTop: 6 }}
                  delay={row * 130 + 60}
                />
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="landing-section" aria-hidden>
        <div className="landing-section-head">
          <Skel w={88} h={22} />
          <Skel w={96} h={13} delay={60} />
        </div>
        <div style={{ marginTop: 14 }}>
          <Skel w="38%" h={11} delay={100} />
        </div>
        <div
          className="site-cards"
          style={{ marginTop: 16, gridTemplateColumns: "repeat(3, minmax(0,1fr))" }}
        >
          <SiteOverviewSkeleton />
          {[0, 1, 2, 3, 4].map((index) => (
            <SiteCardSkeleton key={index} index={index + 1} />
          ))}
        </div>
      </section>

      <section className="landing-section" aria-hidden>
        <div className="landing-section-head">
          <Skel w={110} h={22} />
          <Skel w={78} h={13} delay={60} />
        </div>
        <div style={{ marginTop: 14 }}>
          <Skel w="36%" h={11} delay={100} />
        </div>
        <div style={{ marginTop: 16 }}>
          <RankListSkeleton />
        </div>
      </section>

      <section className="landing-section" aria-hidden>
        <div className="landing-section-head">
          <Skel w={132} h={22} />
        </div>
        <div style={{ display: "grid", gap: 14, marginTop: 18 }}>
          {[0, 1, 2, 3].map((row) => (
            <div key={row} style={{ display: "flex", gap: 14, alignItems: "baseline" }}>
              <Skel w={24} h={13} delay={row * 90} />
              <Skel w={`${54 - row * 5}%`} h={13} delay={row * 90 + 30} />
            </div>
          ))}
        </div>
      </section>

      <section className="landing-section" aria-hidden>
        <div className="landing-section-head">
          <Skel w={64} h={22} />
        </div>
        <div style={{ display: "grid", gap: 14, marginTop: 18 }}>
          {[0, 1, 2].map((row) => (
            <Skel key={row} w={`${44 - row * 4}%`} h={14} delay={row * 100} />
          ))}
        </div>
      </section>

      <section
        className="landing-cta"
        style={{ display: "grid", justifyItems: "center", gap: 18, padding: "48px 0" }}
        aria-hidden
      >
        <Skel w={300} h={24} />
        <Skel w={170} h={44} style={{ borderRadius: 10 }} delay={120} />
      </section>
    </div>
  );
}
