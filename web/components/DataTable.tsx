"use client";

/** 自研数据表：客户端排序（点击表头切换升降序）+ 可选分页 + 横向滚动。 */

import { Fragment, isValidElement, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent, ReactNode } from "react";
import { Btn, Empty, Sel } from "./ui";
import { IconNodes } from "./icons";
import { useNarrow } from "@/lib/useNarrow";
import { PAGE_SIZE_OPTIONS, usePageSize } from "@/lib/usePageSize";

export interface DColumn<T> {
  /** 稳定标识；缺省时取 dataIndex */
  key?: string;
  title: ReactNode;
  dataIndex?: keyof T & string;
  width?: number | string;
  /** 显式上限：浏览器自动布局会把富余空间摊给各列，需要钉死宽度的列（如价格）才设置 */
  maxWidth?: number | string;
  align?: "left" | "right" | "center";
  render?: (value: any, row: T) => ReactNode;
  sorter?: (a: T, b: T) => number;
  defaultSortOrder?: "ascend" | "descend";
  /** true = 窄屏（≤900px，同 .col-hide-m 断点）隐藏该列，保住核心列的可读性 */
  mobileHide?: boolean;
  /** true = 内容自适应截断：撑满单元格宽度，到列的真实边界才出省略号；长文本列（URL、模型名）用 */
  ellipsis?: boolean;
}

function colKey<T>(column: DColumn<T>): string {
  return column.key ?? column.dataIndex ?? String(column.title);
}

/** 页码序列：页数少时全量展示，多时折叠为 1 … 4 5 6 … n */
function pageList(current: number, total: number): (number | "…")[] {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);
  const pages = new Set<number>([1, total, current - 1, current, current + 1]);
  const sorted = [...pages].filter((page) => page >= 1 && page <= total).sort((a, b) => a - b);
  const list: (number | "…")[] = [];
  let previous = 0;
  for (const page of sorted) {
    if (page - previous > 1) list.push("…");
    list.push(page);
    previous = page;
  }
  return list;
}

export function DataTable<T extends object>({
  columns,
  rows,
  rowKey,
  paginated,
  empty = "暂无数据",
  footer,
  scrollX,
  mobileScrollX,
  rowClassName,
  childrenOf,
  fit,
  onRowClick,
}: {
  columns: DColumn<T>[];
  rows: T[];
  rowKey: keyof T & string | ((row: T) => string);
  /** true = 开启分页：每页条数默认 35，选择器可改并全站记住 */
  paginated?: boolean;
  empty?: ReactNode;
  footer?: ReactNode;
  /** 表格最小宽度（px），超出时容器横向滚动 */
  scrollX?: number;
  /** 窄屏（≤900px）的最小宽度：mobileHide 藏列后 minWidth 不必维持桌面值，缺省回落 scrollX */
  mobileScrollX?: number;
  /** 行级 className（如高亮最优行） */
  rowClassName?: (row: T) => string | undefined;
  /** 返回行的子行（如被折叠的同组记录）；子行跟在父行后渲染，不参与排序与分页计数 */
  childrenOf?: (row: T) => readonly T[] | undefined;
  /** true = 表格按内容收缩，不撑满容器；适合列少的汇总表 */
  fit?: boolean;
  /** 点击整行时的回调（如跳转详情页）；行内链接/按钮与行尾展开箭头不受影响 */
  onRowClick?: (row: T) => void;
}) {
  const getKey = typeof rowKey === "string" ? (row: T) => String(row[rowKey]) : rowKey;
  const narrow = useNarrow();
  // 藏列只在窄屏生效，minWidth 同步切换，避免手机上为看不见的列空拖几百像素
  const effectiveScrollX = narrow ? (mobileScrollX ?? scrollX) : scrollX;

  // 横向滚动渐隐提示：内容在某侧被截断时显示渐隐遮罩，让"可以横向滑动"可感知
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [fade, setFade] = useState({ left: false, right: false });

  const updateFade = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    setFade({
      left: el.scrollLeft > 1,
      right: el.scrollLeft + el.clientWidth < el.scrollWidth - 1,
    });
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    updateFade();
    const observer = new ResizeObserver(updateFade);
    observer.observe(el);
    return () => observer.disconnect();
  }, [updateFade]);

  const initial = columns.find((column) => column.defaultSortOrder);
  const [sort, setSort] = useState<{ key: string; dir: "ascend" | "descend" } | null>(
    initial?.sorter ? { key: colKey(initial), dir: initial.defaultSortOrder ?? "ascend" } : null,
  );
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [pageSize, setPageSize] = usePageSize();

  function toggleExpand(key: string) {
    setExpanded((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const column = columns.find((item) => colKey(item) === sort.key);
    if (!column?.sorter) return rows;
    const factor = sort.dir === "ascend" ? 1 : -1;
    return [...rows].sort((a, b) => column.sorter!(a, b) * factor);
  }, [rows, columns, sort]);

  const pageCount = paginated ? Math.max(1, Math.ceil(sorted.length / pageSize)) : 1;
  const current = Math.min(page, pageCount);
  const visible = paginated ? sorted.slice((current - 1) * pageSize, current * pageSize) : sorted;

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
    // 调用方传整只 Empty（带标题/按钮）时原样渲染，否则包一层带默认图标的空状态框
    const emptyNode =
      isValidElement(empty) && empty.type === Empty ? (
        empty
      ) : (
        <Empty icon={<IconNodes size={18} />} description={empty} />
      );
    return (
      <div className="dtable-wrap">
        {emptyNode}
        {footer}
      </div>
    );
  }

  return (
    <div className="dtable-wrap">
      {fade.left && <span className="dtable-fade dtable-fade-l" aria-hidden />}
      {fade.right && <span className="dtable-fade dtable-fade-r" aria-hidden />}
      <div className="dtable-scroll" ref={scrollRef} onScroll={updateFade}>
        {/* 固定布局：列宽即所设值，内容不被挤压；空间不足时靠 minWidth 整体横向滚动 */}
        <table
          className="dtable"
          style={{
            tableLayout: "fixed",
            ...(fit ? { width: "auto" } : null),
            ...(effectiveScrollX ? { minWidth: effectiveScrollX } : null),
          }}
        >
        <thead>
          <tr>
            {columns.map((column) => {
              const key = colKey(column);
              const active = sort?.key === key;
              return (
                <th
                  key={key}
                  style={{ width: column.width, maxWidth: column.maxWidth, textAlign: column.align }}
                  className={`${column.mobileHide ? "col-hide-m" : ""}${column.sorter ? " sortable" : ""}`.trim() || undefined}
                  aria-sort={active ? (sort!.dir === "ascend" ? "ascending" : "descending") : undefined}
                  onClick={() => toggleSort(column)}
                  {...(column.sorter
                    ? {
                        tabIndex: 0,
                        role: "button",
                        onKeyDown: (event: React.KeyboardEvent) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            toggleSort(column);
                          }
                        },
                      }
                    : null)}
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
          {visible.map((row) => {
            const rowKeyValue = getKey(row);
            const children = childrenOf?.(row);
            const hasChildren = !!children && children.length > 0;
            const open = expanded.has(rowKeyValue);
            // 有子行的行：行首名称右侧浮一个 "+N" 徽标作展开入口（数字提示可展开几条），不占独立列
            const renderCells = (record: T, toggle?: ReactNode) =>
              columns.map((column, columnIndex) => {
                const value = column.dataIndex ? record[column.dataIndex] : undefined;
                const content = column.render ? column.render(value, record) : ((value as ReactNode) ?? null);
                return (
                  <td
                    key={colKey(column)}
                    style={{ textAlign: column.align }}
                    className={column.mobileHide ? "col-hide-m" : undefined}
                  >
                    {column.ellipsis ? <span className="cell-ellipsis">{content}</span> : content}
                    {columnIndex === 0 && toggle}
                  </td>
                );
              });
            const rowClass = [
              rowClassName?.(row),
              hasChildren ? "row-expandable" : null,
              onRowClick ? "row-clickable" : null,
            ]
              .filter(Boolean)
              .join(" ") || undefined;
            // 行内链接/按钮点击不触发行为；有 onRowClick 时整行跳详情，展开只靠行尾箭头
            const rowClick = onRowClick
              ? (event: MouseEvent) => {
                  if ((event.target as HTMLElement).closest("a,button")) return;
                  onRowClick(row);
                }
              : hasChildren
                ? (event: MouseEvent) => {
                    if ((event.target as HTMLElement).closest("a,button")) return;
                    toggleExpand(rowKeyValue);
                  }
                : undefined;
            const childClick = (record: T) =>
              onRowClick
                ? (event: MouseEvent) => {
                    if ((event.target as HTMLElement).closest("a,button")) return;
                    onRowClick(record);
                  }
                : undefined;
            return (
              <Fragment key={rowKeyValue}>
                <tr
                  className={rowClass}
                  aria-expanded={hasChildren ? open : undefined}
                  title={onRowClick ? "点击查看站点检测详情" : undefined}
                  onClick={rowClick}
                >
                  {renderCells(
                    row,
                    hasChildren ? (
                      <button
                        type="button"
                        className={`row-toggle${open ? " on" : ""}`}
                        title={open ? "收起" : `展开其余 ${children!.length} 条`}
                        onClick={(event) => {
                          event.stopPropagation();
                          toggleExpand(rowKeyValue);
                        }}
                      >
                        <span aria-hidden>▸</span>
                        {children!.length}
                      </button>
                    ) : undefined,
                  )}
                </tr>
                {hasChildren &&
                  open &&
                  children!.map((child, index) => (
                    <tr
                      key={`${rowKeyValue}:child:${index}`}
                      className={`row-child${onRowClick ? " row-clickable" : ""}`}
                      onClick={childClick(child)}
                    >
                      {renderCells(child)}
                    </tr>
                  ))}
              </Fragment>
            );
          })}
        </tbody>
        </table>
      </div>
      {footer}
      {paginated && (pageCount > 1 || sorted.length > PAGE_SIZE_OPTIONS[0]) && (
        <div className="pager">
          <span className="pager-info">
            共 <span className="mono">{sorted.length}</span> 条
          </span>
          {pageCount > 1 && (
            <>
              <Btn variant="ghost" size="sm" disabled={current <= 1} onClick={() => setPage(current - 1)}>
                上一页
              </Btn>
              {pageList(current, pageCount).map((page, index) =>
                page === "…" ? (
                  <span key={`gap-${index}`} className="pager-gap" aria-hidden>
                    …
                  </span>
                ) : (
                  <button
                    key={page}
                    type="button"
                    className={`pager-page${page === current ? " on" : ""}`}
                    aria-current={page === current ? "page" : undefined}
                    onClick={() => setPage(page)}
                  >
                    {page}
                  </button>
                ),
              )}
              <Btn variant="ghost" size="sm" disabled={current >= pageCount} onClick={() => setPage(current + 1)}>
                下一页
              </Btn>
            </>
          )}
          <label className="pager-size">
            每页
            <Sel
              value={String(pageSize)}
              onChange={(value) => {
                setPageSize(Number(value));
                setPage(1);
              }}
              options={PAGE_SIZE_OPTIONS.map((n) => ({ value: String(n), label: String(n) }))}
              style={{ width: 72 }}
            />
            条
          </label>
        </div>
      )}
    </div>
  );
}
