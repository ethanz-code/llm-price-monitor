"use client";

import { DataTable, type DColumn } from "./DataTable";
import { formatPrice, formatTimeAgo, toCnyPrice, recordStatusKey } from "@/lib/format";
import { chartToneVar, type RateLevel } from "@/lib/channelStatus";
import { getSiteInfo } from "@/lib/sites";
import type { OverviewRecord } from "@/lib/types";
import { RulePriceMark } from "./ToneTag";
import { DiscountBars } from "./DiscountBars";
import { RiskLink } from "./RiskLink";
import { TermTip } from "./TermTip";

/** 单位分母缩写："CNY/1M tokens" → "1M"，只留倍数不带 tokens，避免价格列折行撑高行。 */
function unitSuffix(unit: string | null | undefined): string {
  return unit?.split("/").pop()?.replace(/\s*tokens?$/i, "").trim() ?? "";
}

/** Landing 的最新快照预览表：每模型一行（综合价最低的代表行，见 priceRows.lowestPriceRowPerModel），列渲染含交互，需在客户端渲染。 */
export function SnapshotPreview({
  rows,
  rate,
  siteLevels,
  snapshotAt,
}: {
  rows: OverviewRecord[];
  /** 展示汇率：站点价统一按 RMB 显示，缺失时回落原币数值 */
  rate?: number | null;
  /** 站点当前三档状态（与首页站点墙同口径）：给站名前的小色点供色 */
  siteLevels?: Record<string, RateLevel | null>;
  /** 快照数据新鲜度：全行最新一次采集时间（秒级时间戳） */
  snapshotAt?: number | null;
}) {
  const columns: DColumn<OverviewRecord>[] = [
    {
      title: "站点",
      dataIndex: "site_id",
      // 窄屏自动收缩（vw 上限），375px 首屏三列核心信息尽量不横滚
      width: "min(140px, 24vw)",
      render: (v: string, row) => {
        const site = getSiteInfo(v, row.source_url);
        // 规则价行在站名旁低调标注，首页精选与总览表保持一致的可信度提示
        const statusKey = recordStatusKey(row);
        const level = siteLevels?.[v] ?? null;
        return (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6, minWidth: 0, flexWrap: "wrap" }}>
            {/* 圆点与站名绑成一组不拆行：窄屏列宽收缩时不再上下堆叠，长站名就地省略 */}
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6, minWidth: 0, maxWidth: "100%" }}>
              {level && (
                <span aria-hidden className="site-dot" style={{ background: chartToneVar[level], flexShrink: 0 }} />
              )}
              <RiskLink href={site.homepage || row.source_url} variant="site" title={site.name}>
                <span className="mono" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {site.name}
                </span>
              </RiskLink>
            </span>
            {statusKey === "rule_only" && <RulePriceMark />}
          </span>
        );
      },
    },
    {
      title: "模型",
      dataIndex: "model",
      // auto 布局表格里用 max-width 省略，模型名不把列撑宽
      render: (v: string) => (
        <span
          className="mono"
          title={v}
          style={{ display: "inline-block", maxWidth: "min(170px, 30vw)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", verticalAlign: "bottom" }}
        >
          {v}
        </span>
      ),
    },
    {
      title: (
        <>
          输入价
          <TermTip term="input_price" />
        </>
      ),
      dataIndex: "input_price",
      align: "right",
      width: 130,
      render: (v: number | null, row) => {
        const converted = toCnyPrice(v, row.unit, rate);
        const suffix = unitSuffix(row.unit);
        return (
          <span className="mono" style={{ fontWeight: 550 }}>
            {converted !== null ? "¥" : ""}
            {formatPrice(converted ?? v)}
            {suffix && (
              <span style={{ color: "var(--text-3)", fontSize: 12, fontWeight: 400 }}> /{suffix}</span>
            )}
          </span>
        );
      },
    },
    {
      title: (
        <>
          输出价
          <TermTip term="output_price" />
        </>
      ),
      dataIndex: "output_price",
      align: "right",
      width: 130,
      mobileHide: true,
      render: (v: number | null, row) => {
        const converted = toCnyPrice(v, row.unit, rate);
        const suffix = unitSuffix(row.unit);
        return (
          <span className="mono" style={{ fontWeight: 550 }}>
            {converted !== null ? "¥" : ""}
            {formatPrice(converted ?? v)}
            {suffix && (
              <span style={{ color: "var(--text-3)", fontSize: 12, fontWeight: 400 }}> /{suffix}</span>
            )}
          </span>
        );
      },
    },
    {
      title: "分组",
      key: "group",
      width: 140,
      mobileHide: true,
      render: (_v: unknown, row) => {
        const group = row.metadata?.group;
        return group ? (
          <span className="mono" style={{ fontSize: 12.5, color: "var(--text-2)" }}>
            {group}
          </span>
        ) : (
          <span style={{ color: "var(--text-3)" }}>—</span>
        );
      },
    },
    {
      title: (
        <>
          折扣
          <TermTip term="discount" />
        </>
      ),
      key: "discount",
      width: 150,
      mobileHide: true,
      // 首页快照表用单行紧凑芯片：堆叠条形会把行高撑到两倍
      render: (_, row) => <DiscountBars discount={row.discount} compact />,
    },
  ];

  return (
    <div className="panel" style={{ overflow: "hidden" }}>
      {snapshotAt != null && (
        <div className="snap-head">
          <span className="snap-head-title">
            <span className="snap-head-dot" aria-hidden />
            实时快照
          </span>
          <span className="snap-head-meta">{formatTimeAgo(snapshotAt)}更新</span>
        </div>
      )}
      <DataTable<OverviewRecord>
        rowKey={(row) => `${row.site_id}:${row.model}:${row.unit}:${row.metadata?.group ?? ""}`}
        columns={columns}
        rows={rows}
        dense
      />
    </div>
  );
}
