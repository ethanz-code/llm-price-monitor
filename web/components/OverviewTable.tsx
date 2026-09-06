"use client";

import { useMemo } from "react";
import Link from "next/link";
import { DataTable, type DColumn } from "./DataTable";
import { StatCard } from "./PageHeader";
import { ToneTag } from "./ToneTag";
import { RiskLink } from "./RiskLink";
import { getSiteInfo } from "@/lib/sites";
import { discountTone, formatDiscount, formatPrice, formatTime, statusMeta } from "@/lib/format";
import type { OverviewData, OverviewRecord } from "@/lib/types";

function StatusCell({ row }: { row: OverviewRecord }) {
  const meta = statusMeta(row.price_status);
  const notes = (row.metadata?.notes as string | undefined)?.trim();
  const confidence = row.metadata?.confidence as number | undefined;
  const tip = [notes, confidence !== undefined ? `AI 置信度 ${Math.round(confidence * 100)}%` : ""]
    .filter(Boolean)
    .join(" · ");
  const content = <ToneTag tone={meta.tone}>{meta.label}</ToneTag>;
  return tip ? (
    <span title={tip}>{content}</span>
  ) : (
    content
  );
}

function DiscountCell({ row }: { row: OverviewRecord }) {
  const discount = row.discount;
  if (!discount || (discount.input === null && discount.output === null)) {
    return <span style={{ color: "var(--text-3)" }}>—</span>;
  }
  return (
    <span style={{ display: "inline-flex", gap: 6 }}>
      <ToneTag tone={discountTone(discount.input)}>输入 {formatDiscount(discount.input)}</ToneTag>
      <ToneTag tone={discountTone(discount.output)}>输出 {formatDiscount(discount.output)}</ToneTag>
    </span>
  );
}

export function OverviewTable({ data }: { data: OverviewData }) {
  const records = data.records;

  const stats = useMemo(() => {
    const sites = new Set(records.map((row) => row.site_id));
    const models = new Set(records.map((row) => row.model));
    const latest = records.reduce<number | undefined>(
      (acc, row) => (acc === undefined || row.captured_at > acc ? row.captured_at : acc),
      undefined,
    );
    const inputs = records.map((row) => row.discount?.input).filter((v): v is number => v !== null && v !== undefined);
    const avgInput = inputs.length ? inputs.reduce((a, b) => a + b, 0) / inputs.length : null;
    return { sites: sites.size, models: models.size, latest, avgInput };
  }, [records]);

  const columns: DColumn<OverviewRecord>[] = [
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
      sorter: (a, b) => a.site_id.localeCompare(b.site_id),
    },
    {
      title: "模型",
      dataIndex: "model",
      render: (v: string) => <span className="mono">{v}</span>,
      sorter: (a, b) => a.model.localeCompare(b.model),
    },
    {
      title: "输入价",
      dataIndex: "input_price",
      align: "right",
      sorter: (a, b) => (a.input_price ?? -1) - (b.input_price ?? -1),
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
      sorter: (a, b) => (a.output_price ?? -1) - (b.output_price ?? -1),
      render: (v: number | null) => <span className="mono">{formatPrice(v)}</span>,
    },
    {
      title: "官方价折扣",
      key: "discount",
      sorter: (a, b) => (a.discount?.input ?? 9) - (b.discount?.input ?? 9),
      render: (_, row) => <DiscountCell row={row} />,
    },
    {
      title: "状态",
      dataIndex: "price_status",
      width: 130,
      render: (_, row) => <StatusCell row={row} />,
    },
    {
      title: "采集时间",
      dataIndex: "captured_at",
      width: 160,
      defaultSortOrder: "descend",
      sorter: (a, b) => a.captured_at - b.captured_at,
      render: (v: number) => (
        <span className="mono" style={{ color: "var(--text-2)", fontSize: 13 }}>
          {formatTime(v)}
        </span>
      ),
    },
    {
      title: "",
      dataIndex: "source_url",
      width: 60,
      render: (v: string) => (v ? <RiskLink href={v}>来源</RiskLink> : null),
    },
  ];

  return (
    <>
      <div className="stat-grid rise-in" style={{ marginBottom: 32 }}>
        <StatCard label="监控站点" value={stats.sites} hint="按配置批量采集" />
        <StatCard label="跟踪模型" value={stats.models} hint={`${records.length} 条价格记录`} />
        <StatCard
          label="最近采集"
          value={stats.latest ? formatTime(stats.latest).slice(0, 10) : "—"}
          hint={stats.latest ? formatTime(stats.latest).slice(11) : undefined}
        />
        <StatCard
          label="平均输入折扣"
          value={stats.avgInput !== null ? formatDiscount(stats.avgInput) : "—"}
          hint="相对厂商官方原价"
        />
      </div>
      <div className="panel rise-in" style={{ overflow: "hidden" }}>
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
          <span style={{ fontWeight: 550, fontSize: 15 }}>最新快照</span>
          {data.official.enabled && (
            <span style={{ color: "var(--text-2)", fontSize: 13 }}>
              官方价快照 <span className="mono">{data.official.generated_at_iso ?? "—"}</span> · 汇率{" "}
              <span className="mono">{data.official.usd_cny_rate ?? "—"}</span>（{data.official.rate_source}）·{" "}
              <Link href="/discount" style={{ color: "var(--accent-text)" }}>
                折扣明细
              </Link>
            </span>
          )}
        </div>
        <DataTable<OverviewRecord>
          rowKey={(row) => `${row.site_id}:${row.model}:${row.metadata?.group ?? ""}`}
          columns={columns}
          rows={records}
          scrollX={900}
          empty="暂无价格数据，完成一轮采集后这里会展示各站点最新快照"
        />
      </div>
    </>
  );
}
