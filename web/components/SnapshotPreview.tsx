"use client";

import { DataTable, type DColumn } from "./DataTable";
import { formatDiscount, formatPrice, discountTone, statusMeta } from "@/lib/format";
import { getSiteInfo } from "@/lib/sites";
import type { OverviewRecord } from "@/lib/types";
import { ToneTag } from "./ToneTag";
import { RiskLink } from "./RiskLink";

/** Landing 的最新快照预览表：列渲染含交互，需在客户端渲染。 */
export function SnapshotPreview({ records }: { records: OverviewRecord[] }) {
  const columns: DColumn<OverviewRecord>[] = [
    {
      title: "站点",
      dataIndex: "site_id",
      width: 150,
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
      render: (v: string) => <span className="mono">{v}</span>,
    },
    {
      title: "输入价",
      dataIndex: "input_price",
      align: "right",
      render: (v: number | null, row) => (
        <span className="mono">
          {formatPrice(v)}
          <span style={{ color: "var(--text-3)", fontSize: 12 }}> {row.unit?.split("/").pop()}</span>
        </span>
      ),
    },
    {
      title: "输出价",
      dataIndex: "output_price",
      align: "right",
      render: (v: number | null) => <span className="mono">{formatPrice(v)}</span>,
    },
    {
      title: "折扣",
      key: "discount",
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
    {
      title: "状态",
      dataIndex: "price_status",
      width: 110,
      render: (v: string) => {
        const meta = statusMeta(v);
        return <ToneTag tone={meta.tone}>{meta.label}</ToneTag>;
      },
    },
  ];

  return (
    <div className="panel" style={{ overflow: "hidden" }}>
      <DataTable<OverviewRecord>
        rowKey={(row) => `${row.site_id}:${row.model}:${row.metadata?.group ?? ""}`}
        columns={columns}
        rows={records}
      />
    </div>
  );
}
