"use client";

import { useMemo, useState } from "react";
import { DataTable, type DColumn } from "./DataTable";
import { Btn, Empty, Input, Sel } from "./ui";
import { IconSearch } from "./icons";
import { ToneTag } from "./ToneTag";
import { RiskLink } from "./RiskLink";
import { OfficialRefreshButton } from "./OfficialRefreshButton";
import { formatPrice } from "@/lib/format";
import type { OfficialData, OfficialEntry } from "@/lib/types";

interface Row extends OfficialEntry {
  key: string;
}

export function OfficialTable({ data }: { data: OfficialData }) {
  const [keyword, setKeyword] = useState("");
  const [vendor, setVendor] = useState("all");
  const [onlyFound, setOnlyFound] = useState("found");

  const vendors = useMemo(
    () => Array.from(new Set(Object.values(data.models).map((entry) => entry.vendor))).sort(),
    [data],
  );

  const rows: Row[] = useMemo(() => {
    const lower = keyword.trim().toLowerCase();
    return Object.entries(data.models)
      .map(([key, entry]) => ({ ...entry, key }))
      .filter((row) => (onlyFound === "found" ? row.found : true))
      .filter((row) => (vendor === "all" ? true : row.vendor === vendor))
      .filter((row) => (lower ? `${row.model} ${row.vendor}`.toLowerCase().includes(lower) : true))
      .sort((a, b) => a.vendor.localeCompare(b.vendor) || a.model.localeCompare(b.model));
  }, [data, keyword, vendor, onlyFound]);

  const columns: DColumn<Row>[] = [
    { title: "厂商", dataIndex: "vendor", width: 130, render: (v: string) => <span style={{ fontWeight: 550 }}>{v}</span> },
    { title: "模型", dataIndex: "model", render: (v: string) => <span className="mono">{v}</span> },
    {
      title: "列表价（输入/输出）",
      key: "list",
      align: "right",
      sorter: (a, b) => (a.list?.input ?? 0) - (b.list?.input ?? 0),
      render: (_, row) => (
        <span className="mono">
          ${formatPrice(row.list?.input)} / ${formatPrice(row.list?.output)}
        </span>
      ),
    },
    {
      title: "生效价（输入/输出）",
      key: "effective",
      align: "right",
      sorter: (a, b) => (a.effective?.input ?? 0) - (b.effective?.input ?? 0),
      render: (_, row) => (
        <span
          className="mono"
          style={{ fontWeight: 550 }}
          title={`basis: ${row.effective?.basis ?? "list"}`}
        >
          ${formatPrice(row.effective?.input)} / ${formatPrice(row.effective?.output)}
        </span>
      ),
    },
    {
      title: "促销",
      key: "promo",
      width: 90,
      render: (_, row) => (row.promo ? <ToneTag tone="blue">promo</ToneTag> : <span style={{ color: "var(--text-3)" }}>—</span>),
    },
    {
      title: "来源",
      dataIndex: "source_url",
      width: 90,
      render: (v: string) => (v ? <RiskLink href={v}>定价页</RiskLink> : <span style={{ color: "var(--text-3)" }}>—</span>),
    },
    {
      title: "",
      key: "note",
      width: 100,
      render: (_, row) =>
        row.from_previous_run ? (
          <span
            title="本轮搜索未命中，沿用上一轮结果"
            style={{ fontSize: 12, color: "var(--text-2)" }}
          >
            上轮保留
          </span>
        ) : null,
    },
  ];

  return (
    <div className="section-gap rise-in" style={{ display: "grid", gap: 16 }}>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        <Input
          placeholder="搜索模型或厂商"
          style={{ width: 260 }}
          value={keyword}
          onChange={setKeyword}
          prefix={<IconSearch size={14} />}
        />
        <Sel
          value={vendor}
          onChange={setVendor}
          style={{ width: 160 }}
          options={[{ value: "all", label: "全部厂商" }, ...vendors.map((v) => ({ value: v, label: v }))]}
        />
        <Sel
          value={onlyFound}
          onChange={setOnlyFound}
          style={{ width: 140 }}
          options={[
            { value: "found", label: "只看已找到" },
            { value: "all", label: "包含未找到" },
          ]}
        />
        <span style={{ color: "var(--text-2)", fontSize: 13 }}>
          共 <span className="mono">{Object.keys(data.models).length}</span> 条 · 快照{" "}
          <span className="mono">{data.generated_at_iso}</span> · 汇率{" "}
          <span className="mono">{data.usd_cny_rate}</span>（{data.rate_source}）
        </span>
        <span style={{ marginLeft: "auto" }}>
          <OfficialRefreshButton />
        </span>
      </div>
      <div className="panel" style={{ overflow: "hidden" }}>
        {rows.length === 0 ? (
          <Empty>没有匹配的官方价</Empty>
        ) : (
          <DataTable<Row> rowKey="key" columns={columns} rows={rows} pageSize={20} scrollX={900} />
        )}
      </div>
    </div>
  );
}
