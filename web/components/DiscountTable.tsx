"use client";

import { useMemo } from "react";
import { DataTable, type DColumn } from "./DataTable";
import { StatCard } from "./PageHeader";
import { ToneTag } from "./ToneTag";
import { RiskLink } from "./RiskLink";
import { getSiteInfo } from "@/lib/sites";
import { discountTone, formatDiscount, formatPrice } from "@/lib/format";
import type { DiscountData, DiscountRow, DiscountSummaryItem } from "@/lib/types";

function DiscountTags({ input, output }: { input: number | null; output: number | null }) {
  return (
    <span style={{ display: "inline-flex", gap: 6 }}>
      <ToneTag tone={discountTone(input)}>输入 {formatDiscount(input)}</ToneTag>
      <ToneTag tone={discountTone(output)}>输出 {formatDiscount(output)}</ToneTag>
    </span>
  );
}

export function DiscountTable({ data }: { data: DiscountData }) {
  const summaryRows = useMemo(() => Object.values(data.summary), [data.summary]);
  const best = useMemo(() => {
    const inputs = data.discounts.map((d) => d.input).filter((v): v is number => v !== null);
    return inputs.length ? Math.min(...inputs) : null;
  }, [data.discounts]);

  const summaryColumns: DColumn<DiscountSummaryItem>[] = [
    { title: "模型", dataIndex: "model", render: (v: string) => <span className="mono" style={{ fontWeight: 550 }}>{v}</span> },
    {
      title: "输入折扣（均值）",
      dataIndex: "input_discount",
      key: "input_avg",
      align: "right",
      sorter: (a, b) => a.input_discount.avg - b.input_discount.avg,
      render: (_, row) => (
        <span style={{ display: "inline-flex", gap: 8, alignItems: "baseline" }}>
          <span className="mono" style={{ fontWeight: 550 }}>{formatDiscount(row.input_discount.avg)}</span>
          <span className="mono" style={{ color: "var(--text-3)", fontSize: 12 }}>
            {formatDiscount(row.input_discount.min)} ~ {formatDiscount(row.input_discount.max)}
          </span>
        </span>
      ),
    },
    {
      title: "输出折扣（均值）",
      dataIndex: "output_discount",
      key: "output_avg",
      align: "right",
      sorter: (a, b) => a.output_discount.avg - b.output_discount.avg,
      render: (_, row) => (
        <span style={{ display: "inline-flex", gap: 8, alignItems: "baseline" }}>
          <span className="mono" style={{ fontWeight: 550 }}>{formatDiscount(row.output_discount.avg)}</span>
          <span className="mono" style={{ color: "var(--text-3)", fontSize: 12 }}>
            {formatDiscount(row.output_discount.min)} ~ {formatDiscount(row.output_discount.max)}
          </span>
        </span>
      ),
    },
    { title: "对比站点数", dataIndex: "sites_compared", align: "right", width: 120 },
  ];

  const detailColumns: DColumn<DiscountRow>[] = [
    {
      title: "站点",
      dataIndex: "site_id",
      width: 130,
      render: (v: string, row) => {
        const site = getSiteInfo(v, row.source_url);
        return (
          <RiskLink href={site.homepage || row.source_url} variant="site">
            <span className="mono">{site.name}</span>
          </RiskLink>
        );
      },
    },
    { title: "模型", dataIndex: "model", render: (v: string) => <span className="mono">{v}</span> },
    {
      title: "折扣（输入/输出）",
      key: "discount",
      defaultSortOrder: "ascend",
      sorter: (a, b) => (a.input ?? 9) - (b.input ?? 9),
      render: (_, row) => <DiscountTags input={row.input} output={row.output} />,
    },
    {
      title: "官方价 CNY（输入/输出）",
      key: "official",
      align: "right",
      render: (_, row) => (
        <span className="mono">
          ¥{formatPrice(row.official_input_cny)} / ¥{formatPrice(row.official_output_cny)}
        </span>
      ),
    },
    { title: "基准", dataIndex: "basis", width: 90, render: (v: string) => <span className="mono" style={{ fontSize: 12, color: "var(--text-2)" }}>{v}</span> },
    {
      title: "官方定价页",
      dataIndex: "source_url",
      width: 100,
      render: (v: string) => (v ? <RiskLink href={v}>来源</RiskLink> : <span style={{ color: "var(--text-3)" }}>—</span>),
    },
  ];

  return (
    <div className="section-gap rise-in" style={{ display: "grid", gap: 24 }}>
      <div className="stat-grid">
        <StatCard label="对比条目" value={data.discounts.length} hint={`${data.skipped.length} 条因证据不足跳过`} />
        <StatCard label="最低输入折扣" value={best !== null ? formatDiscount(best) : "—"} hint="全网最便宜的站点价" />
        <StatCard label="覆盖模型" value={summaryRows.length} hint="至少一个站点有可用价" />
        <StatCard label="汇率 USD/CNY" value={data.usd_cny_rate} hint={data.rate_source} />
      </div>

      {summaryRows.length > 0 && (
        <div className="panel" style={{ overflow: "hidden" }}>
          <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--border)", fontWeight: 550, fontSize: 15 }}>
            按模型汇总
          </div>
          <DataTable<DiscountSummaryItem>
            rowKey="model"
            columns={summaryColumns}
            rows={summaryRows}
            scrollX={640}
          />
        </div>
      )}

      <div className="panel" style={{ overflow: "hidden" }}>
        <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--border)", fontWeight: 550, fontSize: 15 }}>
          逐站点明细
        </div>
        <DataTable<DiscountRow>
          rowKey={(row) => `${row.site_id}:${row.model}:${row.group ?? ""}`}
          columns={detailColumns}
          rows={data.discounts}
          scrollX={760}
          empty="暂无可对比的价格"
        />
      </div>

      {data.skipped.length > 0 && (
        <p style={{ color: "var(--text-3)", fontSize: 13, margin: 0 }}>
          未纳入对比 {data.skipped.length} 条：
          {data.skipped
            .slice(0, 8)
            .map((item) => `${item.site_id ?? "?"}/${item.model ?? "?"}（${item.reason}）`)
            .join("、")}
          {data.skipped.length > 8 ? " 等" : ""}
        </p>
      )}
    </div>
  );
}
