"use client";

import { useMemo, useState } from "react";
import { DataTable, type DColumn } from "./DataTable";
import { Empty, Input, Pick } from "./ui";
import { IconSearch } from "./icons";
import { RiskLink } from "./RiskLink";
import { TermTip } from "./TermTip";
import { ToneTag } from "./ToneTag";
import { VENDOR_LOGOS } from "@/lib/vendor-logos";
import { formatPrice, formatTokens, isFreePrice, looseIncludes } from "@/lib/format";
import type { CatalogData, CatalogEntry } from "@/lib/types";

interface Row extends CatalogEntry {
  key: string;
}

/** 厂商显示名 → vendor-logos.ts 的 slug key；新厂商未收录时回落首字母徽标。 */
const VENDOR_KEY: Record<string, string> = {
  "alibaba cloud": "alibabacloud",
  anthropic: "anthropic",
  deepseek: "deepseek",
  google: "google",
  "moonshot ai": "moonshot",
  openai: "openai",
  "zhipu ai": "zhipu",
  xai: "xai",
};

/** 厂商 Logo：全量渠道优先用 models.dev 托管的品牌图，加载失败回落；官方视图走本地内置 SVG；都没有则首字母色块。 */
export function VendorBadge({ vendor, logo }: { vendor: string; logo?: string | null }) {
  const [failed, setFailed] = useState(false);
  if (logo && !failed) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={logo}
        alt=""
        aria-hidden
        title={vendor}
        loading="lazy"
        onError={() => setFailed(true)}
        className="vendor-logo"
        style={{ objectFit: "contain" }}
      />
    );
  }
  const key = VENDOR_KEY[vendor.trim().toLowerCase()];
  const svg = key ? VENDOR_LOGOS[key] : undefined;
  if (svg) {
    return (
      <span
        aria-hidden
        className="vendor-logo"
        title={vendor}
        dangerouslySetInnerHTML={{ __html: svg }}
      />
    );
  }
  const initial = vendor.trim().charAt(0).toUpperCase() || "?";
  let hash = 0;
  for (const ch of vendor) hash = (hash * 31 + ch.charCodeAt(0)) % 360;
  return (
    <span
      aria-hidden
      className="vendor-badge"
      style={{ background: `hsl(${hash} 42% 46% / 0.18)`, color: `hsl(${hash} 55% 62%)` }}
    >
      {initial}
    </span>
  );
}

export function CatalogTable({ data }: { data: CatalogData }) {
  const [keyword, setKeyword] = useState("");
  const [vendor, setVendor] = useState("all");

  const vendors = useMemo(
    () => Array.from(new Set(Object.values(data.models).map((entry) => entry.vendor))),
    [data],
  );

  const rows: Row[] = useMemo(() => {
    const lower = keyword.trim().toLowerCase();
    return Object.entries(data.models)
      .map(([key, entry]) => ({ ...entry, key }))
      .filter((row) => row.found)
      .filter((row) => (vendor === "all" ? true : row.vendor === vendor))
      .filter((row) => (lower ? looseIncludes(`${row.model} ${row.vendor} ${row.name ?? ""}`, lower) : true));
    // 模糊匹配忽略 - _ . 空格等分隔符：搜 GLM5.3 也能命中 GLM-5.3；不重排，保留后端权威顺序
  }, [data, keyword, vendor]);

  const columns: DColumn<Row>[] = [
    {
      title: "厂商",
      dataIndex: "vendor",
      width: 170,
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
      width: 220,
      render: (v: string, row: Row) => (
        <span style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
          <span
            className="mono"
            title={v}
            style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
          >
            {v}
          </span>
          {row.tier === "mainstream" && <ToneTag tone="gray">主流</ToneTag>}
        </span>
      ),
    },
    {
      title: (
        <>
          厂商价（输入/输出）<TermTip term="list_price" />
          <span className="thead-unit thead-unit-block">USD / 1M tokens</span>
        </>
      ),
      key: "list",
      align: "right",
      width: 190,
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
          换算价（输入/输出）<TermTip term="official_cny" />
          <span className="thead-unit thead-unit-block">CNY / 1M tokens</span>
        </>
      ),
      key: "list_cny",
      align: "right",
      width: 190,
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
          上下文（最大/输出）<TermTip term="context_limit" />
          <span className="thead-unit thead-unit-block">tokens</span>
        </>
      ),
      key: "limit",
      align: "right",
      width: 190,
      mobileHide: true,
      sorter: (a, b) => (a.limit?.context ?? 0) - (b.limit?.context ?? 0),
      render: (_, row) => {
        const limit = row.limit;
        if (!limit?.context) return <span style={{ color: "var(--text-3)" }}>—</span>;
        const part = (value: number | undefined) => (value ? formatTokens(value) : "—");
        const full = (value: number | undefined) => (value ? value.toLocaleString() : "—");
        return (
          <span
            className="mono num"
            title={`上下文 ${limit.context.toLocaleString()} · 最大输出 ${full(limit.output)}`}
          >
            {formatTokens(limit.context)} / {part(limit.output)}
          </span>
        );
      },
    },
    {
      title: (
        <>
          模型简介
          <TermTip term="description" />
        </>
      ),
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
          placeholder="搜索模型或厂商"
          style={{ flex: "1 1 260px", maxWidth: "min(420px, 100%)" }}
          value={keyword}
          onChange={setKeyword}
          prefix={<IconSearch size={14} />}
        />
        <Pick
          value={vendor}
          onChange={setVendor}
          style={{ width: 160, maxWidth: "100%" }}
          options={[{ value: "all", label: "全部厂商" }, ...vendors.map((v) => ({ value: v, label: v }))]}
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
          <span style={{ fontWeight: 550, fontSize: 15 }}>厂商定价快照</span>
          <span style={{ color: "var(--text-2)", fontSize: 13 }}>
            共 <span className="mono">{Object.values(data.models).filter((entry) => entry.found).length}</span> 条 · 快照{" "}
            <span className="mono">{data.generated_at_iso}</span> · 汇率{" "}
            <span className="mono">{data.usd_cny_rate}</span>（{data.rate_source}）· 来源{" "}
            <RiskLink href={data.source_url || "https://models.dev"}>models.dev</RiskLink>
          </span>
        </div>
        {rows.length === 0 ? (
          <Empty icon={<IconSearch size={18} />} title="没有匹配的厂商价" description="换个关键词或调整筛选条件再试试。" />
        ) : (
          <DataTable<Row>
            rowKey="key"
            columns={columns}
            rows={rows}
            paginated
            scrollX={1410}
            mobileScrollX={770}
            rowClassName={(row) => (row.tier === "flagship" ? "row-flagship" : undefined)}
          />
        )}
      </div>
    </div>
  );
}
