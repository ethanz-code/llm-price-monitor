"use client";

import { useMemo, useState } from "react";
import { DataTable, type DColumn } from "./DataTable";
import { Empty, Input, Sel } from "./ui";
import { IconSearch } from "./icons";
import { RiskLink } from "./RiskLink";
import { ToneTag } from "./ToneTag";
import { VendorBadge } from "./CatalogTable";
import { formatPrice, formatTokens, isFreePrice } from "@/lib/format";
import type { CatalogData, CatalogEntry } from "@/lib/types";

interface Row extends CatalogEntry {
  key: string;
}

/** 全量渠道价表：models.dev 所有厂商的带价模型，仅供浏览，不做旗舰高光。 */
export function CatalogAllTable({ data }: { data: CatalogData }) {
  const [keyword, setKeyword] = useState("");
  const [vendor, setVendor] = useState("all");

  const vendors = useMemo(
    () => Array.from(new Set(Object.values(data.models).map((entry) => entry.vendor))).sort((a, b) => a.localeCompare(b)),
    [data],
  );

  const rows: Row[] = useMemo(() => {
    const lower = keyword.trim().toLowerCase();
    return Object.entries(data.models)
      .map(([key, entry]) => ({ ...entry, key }))
      .filter((row) => row.found)
      .filter((row) => (vendor === "all" ? true : row.vendor === vendor))
      .filter((row) => (lower ? `${row.model} ${row.vendor} ${row.name ?? ""}`.toLowerCase().includes(lower) : true));
  }, [data, keyword, vendor]);

  const columns: DColumn<Row>[] = [
    {
      title: "渠道 / 厂商",
      dataIndex: "vendor",
      width: 190,
      render: (v: string) => (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8, fontWeight: 550, whiteSpace: "nowrap" }}>
          <VendorBadge vendor={v} />
          {v}
        </span>
      ),
    },
    {
      title: "模型",
      dataIndex: "model",
      width: 230,
      render: (v: string, row: Row) => (
        <span
          className="mono"
          title={row.name ? `${row.name}（${v}）` : v}
          style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
        >
          {v}
        </span>
      ),
    },
    {
      title: (
        <>
          价格（输入/输出）
          <span className="thead-unit thead-unit-block">USD / 1M tokens</span>
        </>
      ),
      key: "list",
      align: "right",
      width: 180,
      sorter: (a, b) => (a.list?.input ?? 0) - (b.list?.input ?? 0),
      render: (_, row) =>
        isFreePrice(row.list) ? (
          <ToneTag tone="green">免费</ToneTag>
        ) : (
          <span className="mono num" style={{ fontWeight: 550 }}>
            ${formatPrice(row.list?.input)} / ${formatPrice(row.list?.output)}
          </span>
        ),
    },
    {
      title: (
        <>
          换算价（输入/输出）
          <span className="thead-unit thead-unit-block">CNY / 1M tokens</span>
        </>
      ),
      key: "list_cny",
      align: "right",
      width: 180,
      sorter: (a, b) => (a.list_cny?.input ?? 0) - (b.list_cny?.input ?? 0),
      render: (_, row) =>
        isFreePrice(row.list) ? (
          <span style={{ color: "var(--text-3)" }}>免费</span>
        ) : (
          <span className="mono num" style={{ color: "var(--text-2)" }}>
            ¥{formatPrice(row.list_cny?.input)} / ¥{formatPrice(row.list_cny?.output)}
          </span>
        ),
    },
    {
      title: (
        <>
          上下文（最大/输出）
          <span className="thead-unit thead-unit-block">tokens</span>
        </>
      ),
      key: "limit",
      align: "right",
      width: 180,
      mobileHide: true,
      sorter: (a, b) => (a.limit?.context ?? 0) - (b.limit?.context ?? 0),
      render: (_, row) => {
        const limit = row.limit;
        if (!limit?.context) return <span style={{ color: "var(--text-3)" }}>—</span>;
        const part = (value: number | undefined) => (value ? formatTokens(value) : "—");
        return (
          <span className="mono num">
            {formatTokens(limit.context)} / {part(limit.output)}
          </span>
        );
      },
    },
    {
      title: "模型简介",
      key: "description",
      mobileHide: true,
      render: (_, row) => {
        const text = row.description_zh || row.description;
        return text ? (
          <span
            title={text}
            style={{
              display: "inline-block",
              maxWidth: "100%",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              verticalAlign: "bottom",
              color: "var(--text-2)",
            }}
          >
            {text}
          </span>
        ) : (
          <span style={{ color: "var(--text-3)" }}>—</span>
        );
      },
    },
    {
      title: "发布",
      dataIndex: "release_date",
      width: 110,
      mobileHide: true,
      sorter: (a, b) => (a.release_date ?? "").localeCompare(b.release_date ?? ""),
      render: (v: string | null | undefined) =>
        v ? (
          <span className="mono num">{v}</span>
        ) : (
          <span style={{ color: "var(--text-3)" }}>—</span>
        ),
    },
    {
      title: "来源",
      dataIndex: "source_url",
      width: 60,
      mobileHide: true,
      render: (v: string) => (v ? <RiskLink href={v}>定价页</RiskLink> : <span style={{ color: "var(--text-3)" }}>—</span>),
    },
  ];

  return (
    <div className="section-gap rise-in" style={{ display: "grid", gap: 16 }}>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        <Input
          placeholder="搜索模型、名称或渠道"
          style={{ flex: "1 1 260px", maxWidth: "min(420px, 100%)" }}
          value={keyword}
          onChange={setKeyword}
          prefix={<IconSearch size={14} />}
        />
        <Sel
          value={vendor}
          onChange={setVendor}
          style={{ width: 200, maxWidth: "100%" }}
          options={[{ value: "all", label: "全部渠道" }, ...vendors.map((v) => ({ value: v, label: v }))]}
        />
      </div>
      <div className="panel" style={{ overflow: "hidden" }}>
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
          <span style={{ fontWeight: 550, fontSize: 15 }}>全量渠道价格</span>
          <span style={{ color: "var(--text-2)", fontSize: 13 }}>
            共 <span className="mono">{Object.values(data.models).filter((entry) => entry.found).length}</span> 条 ·{" "}
            <span className="mono">{vendors.length}</span> 个渠道 · 快照{" "}
            <span className="mono">{data.generated_at_iso}</span> · 汇率{" "}
            <span className="mono">{data.usd_cny_rate}</span>（{data.rate_source}）· 来源{" "}
            <RiskLink href={data.source_url || "https://models.dev"}>models.dev</RiskLink>
          </span>
        </div>
        {rows.length === 0 ? (
          <Empty icon={<IconSearch size={18} />} title="没有匹配的渠道价" description="换个关键词或调整筛选条件再试试。" />
        ) : (
          <DataTable<Row> rowKey="key" columns={columns} rows={rows} paginated scrollX={1310} mobileScrollX={740} />
        )}
      </div>
    </div>
  );
}
