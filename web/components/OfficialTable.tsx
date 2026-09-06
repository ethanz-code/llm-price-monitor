"use client";

import { useMemo, useState } from "react";
import { Empty, Input, Select, Table, Tooltip, Typography } from "antd";
import { SearchOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { formatPrice } from "@/lib/format";
import type { OfficialData, OfficialEntry } from "@/lib/types";
import { OfficialRefreshButton } from "./OfficialRefreshButton";
import { ToneTag } from "./ToneTag";
import { RiskLink } from "./RiskLink";

interface Row extends OfficialEntry {
  key: string;
}

export function OfficialTable({ data }: { data: OfficialData }) {
  const [keyword, setKeyword] = useState("");
  const [vendor, setVendor] = useState("all");
  const [onlyFound, setOnlyFound] = useState(true);

  const vendors = useMemo(
    () => Array.from(new Set(Object.values(data.models).map((entry) => entry.vendor))).sort(),
    [data],
  );

  const rows: Row[] = useMemo(() => {
    const lower = keyword.trim().toLowerCase();
    return Object.entries(data.models)
      .map(([key, entry]) => ({ ...entry, key }))
      .filter((row) => (onlyFound ? row.found : true))
      .filter((row) => (vendor === "all" ? true : row.vendor === vendor))
      .filter((row) => (lower ? `${row.model} ${row.vendor}`.toLowerCase().includes(lower) : true))
      .sort((a, b) => a.vendor.localeCompare(b.vendor) || a.model.localeCompare(b.model));
  }, [data, keyword, vendor, onlyFound]);

  const columns: ColumnsType<Row> = [
    { title: "厂商", dataIndex: "vendor", width: 130, render: (v: string) => <span style={{ fontWeight: 550 }}>{v}</span> },
    { title: "模型", dataIndex: "model", render: (v: string) => <span className="mono">{v}</span> },
    {
      title: "列表价（输入/输出）",
      key: "list",
      align: "right",
      render: (_, row) => (
        <span className="mono">
          ${formatPrice(row.list?.input)} / ${formatPrice(row.list?.output)}
        </span>
      ),
      sorter: (a, b) => (a.list?.input ?? 0) - (b.list?.input ?? 0),
    },
    {
      title: "生效价（输入/输出）",
      key: "effective",
      align: "right",
      render: (_, row) => (
        <Tooltip title={`basis: ${row.effective?.basis ?? "list"}`}>
          <span className="mono" style={{ fontWeight: 550 }}>
            ${formatPrice(row.effective?.input)} / ${formatPrice(row.effective?.output)}
          </span>
        </Tooltip>
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
          <Tooltip title="本轮搜索未命中，沿用上一轮结果">
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              上轮保留
            </Typography.Text>
          </Tooltip>
        ) : null,
    },
  ];

  return (
    <div className="section-gap rise-in" style={{ display: "grid", gap: 16 }}>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        <Input
          allowClear
          prefix={<SearchOutlined style={{ color: "var(--text-3)" }} />}
          placeholder="搜索模型或厂商"
          style={{ width: 260 }}
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
        />
        <Select
          value={vendor}
          onChange={setVendor}
          style={{ width: 160 }}
          options={[{ value: "all", label: "全部厂商" }, ...vendors.map((v) => ({ value: v, label: v }))]}
        />
        <Select
          value={onlyFound ? "found" : "all"}
          onChange={(value) => setOnlyFound(value === "found")}
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
        <Table<Row>
          columns={columns}
          dataSource={rows}
          pagination={{ pageSize: 20, showSizeChanger: false }}
          size="middle"
          scroll={{ x: 900 }}
          locale={{ emptyText: <Empty description="没有匹配的官方价" /> }}
        />
      </div>
    </div>
  );
}
