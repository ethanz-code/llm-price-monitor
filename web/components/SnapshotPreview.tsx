"use client";

import { DataTable, type DColumn } from "./DataTable";
import { formatDiscount, formatPrice, discountTone, toCnyPrice } from "@/lib/format";
import { modelRowKey } from "@/lib/priceRows";
import { getSiteInfo } from "@/lib/sites";
import type { OverviewRecord } from "@/lib/types";
import { ToneTag } from "./ToneTag";
import { RiskLink } from "./RiskLink";
import { TermTip } from "./TermTip";

/** Landing 的最新快照预览表：与价格总览同一套合并口径（同站点同模型取最低价一行，+N 展开），列渲染含交互，需在客户端渲染。 */
export function SnapshotPreview({
  rows,
  childRowsOf,
  rate,
}: {
  rows: OverviewRecord[];
  childRowsOf: Map<string, OverviewRecord[]>;
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
        return (
          <RiskLink href={site.homepage || row.source_url} variant="site">
            <span className="mono">{site.name}</span>
          </RiskLink>
        );
      },
    },
    {
      title: "模型",
      dataIndex: "model",
      // auto 布局表格里用 max-width 省略，模型名不把列撑宽
      render: (v: string, row) => {
        const rest = childRowsOf.get(modelRowKey(row));
        return (
          <span style={{ display: "flex", alignItems: "center", gap: 4, minWidth: 0 }}>
            <span
              className="mono"
              title={v}
              style={{ display: "inline-block", maxWidth: "min(170px, 30vw)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", verticalAlign: "bottom" }}
            >
              {v}
            </span>
            {rest && !rest.includes(row) && (
              <span title={`还有 ${rest.length} 条记录，展开查看`} style={{ color: "var(--text-3)", fontSize: 12, flexShrink: 0 }}>
                +{rest.length}
              </span>
            )}
          </span>
        );
      },
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
        childrenOf={(row) => childRowsOf.get(modelRowKey(row))}
      />
    </div>
  );
}
