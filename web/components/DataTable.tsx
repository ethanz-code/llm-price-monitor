"use client";

/** 自研数据表：客户端排序（点击表头切换升降序）+ 可选分页 + 横向滚动。 */

import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Btn } from "./ui";

export interface DColumn<T> {
  /** 稳定标识；缺省时取 dataIndex */
  key?: string;
  title: ReactNode;
  dataIndex?: keyof T & string;
  width?: number | string;
  align?: "left" | "right" | "center";
  render?: (value: any, row: T) => ReactNode;
  sorter?: (a: T, b: T) => number;
  defaultSortOrder?: "ascend" | "descend";
}

function colKey<T>(column: DColumn<T>): string {
  return column.key ?? column.dataIndex ?? String(column.title);
}

export function DataTable<T extends object>({
  columns,
  rows,
  rowKey,
  pageSize,
  empty = "暂无数据",
  footer,
  scrollX,
}: {
  columns: DColumn<T>[];
  rows: T[];
  rowKey: keyof T & string | ((row: T) => string);
  pageSize?: number;
  empty?: ReactNode;
  footer?: ReactNode;
  /** 表格最小宽度（px），超出时容器横向滚动 */
  scrollX?: number;
}) {
  const getKey = typeof rowKey === "string" ? (row: T) => String(row[rowKey]) : rowKey;

  const initial = columns.find((column) => column.defaultSortOrder);
  const [sort, setSort] = useState<{ key: string; dir: "ascend" | "descend" } | null>(
    initial?.sorter ? { key: colKey(initial), dir: initial.defaultSortOrder ?? "ascend" } : null,
  );
  const [page, setPage] = useState(1);

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const column = columns.find((item) => colKey(item) === sort.key);
    if (!column?.sorter) return rows;
    const factor = sort.dir === "ascend" ? 1 : -1;
    return [...rows].sort((a, b) => column.sorter!(a, b) * factor);
  }, [rows, columns, sort]);

  const pageCount = pageSize ? Math.max(1, Math.ceil(sorted.length / pageSize)) : 1;
  const current = Math.min(page, pageCount);
  const visible = pageSize ? sorted.slice((current - 1) * pageSize, current * pageSize) : sorted;

  function toggleSort(column: DColumn<T>) {
    if (!column.sorter) return;
    const key = colKey(column);
    setSort((previous) => {
      if (previous?.key !== key) return { key, dir: "ascend" };
      if (previous.dir === "ascend") return { key, dir: "descend" };
      return null;
    });
    setPage(1);
  }

  if (rows.length === 0) {
    return (
      <div className="dtable-wrap">
        <div className="empty">{empty}</div>
        {footer}
      </div>
    );
  }

  return (
    <div className="dtable-wrap">
      <table className="dtable" style={scrollX ? { minWidth: scrollX } : undefined}>
        <thead>
          <tr>
            {columns.map((column) => {
              const key = colKey(column);
              const active = sort?.key === key;
              return (
                <th
                  key={key}
                  style={{ width: column.width, textAlign: column.align }}
                  className={column.sorter ? "sortable" : undefined}
                  aria-sort={active ? (sort!.dir === "ascend" ? "ascending" : "descending") : undefined}
                  onClick={() => toggleSort(column)}
                >
                  {column.title}
                  {column.sorter && (
                    <span className={`sort${active ? " on" : ""}`} aria-hidden>
                      {active ? (sort!.dir === "ascend" ? "↑" : "↓") : "↕"}
                    </span>
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {visible.map((row) => (
            <tr key={getKey(row)}>
              {columns.map((column) => {
                const value = column.dataIndex ? row[column.dataIndex] : undefined;
                return (
                  <td key={colKey(column)} style={{ textAlign: column.align }}>
                    {column.render ? column.render(value, row) : ((value as ReactNode) ?? null)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {footer}
      {pageSize && pageCount > 1 && (
        <div className="pager">
          <Btn variant="ghost" size="sm" disabled={current <= 1} onClick={() => setPage(current - 1)}>
            上一页
          </Btn>
          <span>
            {current} / {pageCount}
          </span>
          <Btn variant="ghost" size="sm" disabled={current >= pageCount} onClick={() => setPage(current + 1)}>
            下一页
          </Btn>
        </div>
      )}
    </div>
  );
}
