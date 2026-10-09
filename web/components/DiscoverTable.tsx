"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { DataTable, type DColumn } from "./DataTable";
import { Empty, Input } from "./ui";
import { IconSearch } from "./icons";
import { DajuSit } from "./DajuArt";
import { looseIncludes } from "@/lib/format";
import type { DiscoveryData, DiscoveryStation } from "@/lib/types";

type LibraryFilter = "all" | "imported" | "pending";

const FILTERS: { key: LibraryFilter; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "imported", label: "已在监控" },
  { key: "pending", label: "未监控" },
];

/** 站点简介：导航站收录的描述优先，其次站点自报站名（new-api 默认名没有信息量，不当简介） */
function briefOf(row: DiscoveryStation): string {
  if (row.description) return row.description;
  const generic = /^(new[- ]?api|one[- ]?api|api|llm)$/i.test(row.system_name.trim());
  return generic ? "" : row.system_name;
}

/** 新站发现表：price-discover 探测通过的中转站清单，标注是否已进入本站监控。 */
export function DiscoverTable({ data }: { data: DiscoveryData }) {
  const [keyword, setKeyword] = useState("");
  const [filter, setFilter] = useState<LibraryFilter>("all");

  const rows = useMemo<DiscoveryStation[]>(() => {
    let all = data.stations;
    if (filter === "imported") all = all.filter((row) => row.imported_id);
    if (filter === "pending") all = all.filter((row) => !row.imported_id);
    if (!keyword.trim()) return all;
    return all.filter(
      (row) => looseIncludes(row.host, keyword) || looseIncludes(row.name, keyword) || looseIncludes(row.description, keyword),
    );
  }, [data.stations, filter, keyword]);

  const columns: DColumn<DiscoveryStation>[] = [
    {
      title: "中转站",
      dataIndex: "host",
      width: 240,
      ellipsis: true,
      render: (v: string, row) => (
        <a href={row.url} target="_blank" rel="noreferrer" className="mono" title={row.url} style={{ color: "inherit", textDecorationColor: "var(--border-strong)" }}>
          {v}
        </a>
      ),
    },
    {
      title: "监控状态",
      dataIndex: "imported_id",
      width: 110,
      // 手机上整列几乎全是「未监控」，价值不如模型数；监控与否用筛选 tab 表达
      mobileHide: true,
      render: (v: string | null) =>
        v ? (
          <Link href={`/overview/status/${encodeURIComponent(v)}`} style={{ color: "var(--accent-text)" }}>
            已监控
          </Link>
        ) : (
          <span style={{ color: "var(--text-3)" }}>未监控</span>
        ),
    },
    {
      title: "简介",
      dataIndex: "description",
      width: 420,
      ellipsis: true,
      mobileHide: true,
      render: (_: string, row) => {
        const brief = briefOf(row);
        return brief ? (
          <span title={brief} style={{ color: "var(--text-2)", fontSize: 13 }}>
            {brief}
          </span>
        ) : (
          <span style={{ color: "var(--text-3)" }}>—</span>
        );
      },
    },
  ];

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center" }}>
        <Input
          placeholder="搜索站点域名、名称或简介"
          style={{ flex: "1 1 260px", maxWidth: "min(420px, 100%)" }}
          value={keyword}
          onChange={setKeyword}
          prefix={<IconSearch size={14} />}
        />
        <div role="group" aria-label="按监控状态筛选" style={{ display: "flex", gap: 6 }}>
          {FILTERS.map((item) => {
            const active = filter === item.key;
            return (
              <button
                key={item.key}
                type="button"
                aria-pressed={active}
                onClick={() => setFilter(item.key)}
                style={{
                  border: "1px solid var(--border)",
                  borderRadius: 999,
                  padding: "5px 14px",
                  fontSize: 13,
                  cursor: "pointer",
                  background: active ? "var(--accent-text)" : "transparent",
                  color: active ? "var(--bg, #fff)" : "var(--text-2)",
                  borderColor: active ? "var(--accent-text)" : "var(--border)",
                }}
              >
                {item.label}
              </button>
            );
          })}
        </div>
        <span style={{ color: "var(--text-2)", fontSize: 13 }}>
          数据时间 <span className="mono">{data.generated_at || "—"}</span> · 「公开」= 价格页无需登录，未探测与失联站不占列表
        </span>
      </div>
      <div className="panel rise-in" style={{ overflow: "hidden" }}>
        <DataTable<DiscoveryStation>
          rowKey={(row) => row.host}
          columns={columns}
          rows={rows}
          paginated
          defaultPageSize={100}
          pageSizeStorageKey="discover-page-size"
          scrollX={760}
          mobileScrollX={390}
          dense
          empty={
            <Empty
              icon={<DajuSit width={30} />}
              title="没有匹配的中转站"
              description="换个关键词试试，或清空搜索框看完整清单。"
            />
          }
        />
      </div>
    </div>
  );
}
