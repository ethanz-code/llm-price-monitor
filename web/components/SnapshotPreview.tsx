"use client";

import { DataTable, type DColumn } from "./DataTable";
import { formatDiscount, formatPrice, discountTone, toCnyPrice, recordStatusKey } from "@/lib/format";
import { getSiteInfo } from "@/lib/sites";
import type { OverviewRecord } from "@/lib/types";
import { ToneTag, RulePriceMark } from "./ToneTag";
import { RiskLink } from "./RiskLink";
import { TermTip } from "./TermTip";

/** Landing 的最新快照预览表：与价格总览同一口径（不折叠，各分组各占一行），列渲染含交互，需在客户端渲染。 */
export function SnapshotPreview({
  rows,
  rate,
}: {
  rows: OverviewRecord[];
  /** 展示汇率：站点价统一按 RMB 显示，缺失时回落原币数值 */
  rate?: number | null;
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
        return (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6, minWidth: 0, flexWrap: "wrap" }}>
            <RiskLink href={site.homepage || row.source_url} variant="site">
              <span className="mono">{site.name}</span>
            </RiskLink>
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
        return (
          <span className="mono">
            {converted !== null ? "¥" : ""}
            {formatPrice(converted ?? v)}
            <span style={{ color: "var(--text-3)", fontSize: 12 }}> {row.unit?.split("/").pop()}</span>
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
        return (
          <span className="mono">
            {converted !== null ? "¥" : ""}
            {formatPrice(converted ?? v)}
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
      render: (_, row) =>
        row.discount ? (
          <span style={{ display: "inline-flex", gap: 6 }}>
            <ToneTag tone={discountTone(row.discount.input)}>入 {formatDiscount(row.discount.input)}</ToneTag>
            <ToneTag tone={discountTone(row.discount.output)}>出 {formatDiscount(row.discount.output)}</ToneTag>
          </span>
        ) : (
          <span style={{ color: "var(--text-3)" }}>—</span>
        ),
    },
  ];

  return (
    <div className="panel" style={{ overflow: "hidden" }}>
      <DataTable<OverviewRecord>
        rowKey={(row) => `${row.site_id}:${row.model}:${row.unit}:${row.metadata?.group ?? ""}`}
        columns={columns}
        rows={rows}
      />
    </div>
  );
}
