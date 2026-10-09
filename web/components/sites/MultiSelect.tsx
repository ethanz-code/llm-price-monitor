"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { IconCheck } from "../icons";
import { apiSend } from "@/lib/api";
import { looseIncludes } from "@/lib/format";
import type { CatalogData } from "@/lib/types";

/** 多选下拉的候选项：id 是勾选与标签用的值，note 是右侧的补充说明（模型名/厂商等）。 */
interface MultiOption {
  id: string;
  note?: string;
}

/** 一次最多渲染的匹配项：候选数百条时全部渲染没必要。 */
const MODEL_MATCH_LIMIT = 60;

const MODEL_HINT_STYLE: React.CSSProperties = { fontSize: 12.5, color: "var(--text-3)", padding: "6px 8px" };

/** 标签式多选通用壳：已选项以标签展示、点 × 移除；下拉里输入过滤、勾选候选、回车把输入加为自定义项。
 *  options 为 null 表示候选还在加载，空数组表示没有候选只能手动输入。文案由调用方给，模型目录与分组白名单共用。 */
function TagMultiSelect({
  value,
  onChange,
  options,
  failed,
  ariaLabel,
  inputLabel,
  fieldPlaceholder,
  inputPlaceholder,
  loadingText,
  emptyText,
  failedText,
  noMatchText,
  queryHint,
  width = "min(420px, 100%)",
}: {
  value: string[];
  onChange: (next: string[]) => void;
  options: MultiOption[] | null;
  /** 候选拉取失败：空态文案换成"没拉到"口径 */
  failed?: boolean;
  /** 外层与下拉共用的宽度；默认 420px，宽版场景（如监控模型行）自行加宽 */
  ariaLabel: string;
  inputLabel: string;
  fieldPlaceholder: string;
  inputPlaceholder: string;
  loadingText: string;
  emptyText: string;
  failedText: string;
  noMatchText: string;
  /** 按当前输入给一行提示；返回 null 不显示 */
  queryHint?: (query: string) => string | null;
  width?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const hint = queryHint?.(query) ?? null;

  const selected = useMemo(() => new Set(value), [value]);
  const keyword = query.trim().toLowerCase();
  const matches = useMemo(() => {
    const source = options ?? [];
    if (!keyword) return source.slice(0, MODEL_MATCH_LIMIT);
    const hit = source.filter((item) => looseIncludes(item.id, keyword) || looseIncludes(item.note ?? "", keyword));
    // 手动添加的名字不在候选里：把命中的已选项补进来，保证始终可见可移除
    for (const id of value) {
      if (looseIncludes(id, keyword) && !hit.some((item) => item.id === id)) {
        hit.unshift({ id });
      }
    }
    return hit.slice(0, MODEL_MATCH_LIMIT);
  }, [options, keyword, value]);

  function toggle(id: string) {
    onChange(selected.has(id) ? value.filter((item) => item !== id) : [...value, id]);
  }

  function commitInput() {
    const id = query.trim();
    setQuery("");
    if (!id || selected.has(id)) return;
    onChange([...value, id]);
  }

  return (
    <span style={{ position: "relative", display: "inline-flex", width }}>
      <div
        role="button"
        aria-label={ariaLabel}
        onClick={() => setOpen(true)}
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 6,
          alignItems: "center",
          width: "100%",
          minHeight: 34,
          padding: "5px 10px",
          borderRadius: 6,
          border: "1px solid var(--border-strong)",
          background: "var(--panel)",
          cursor: "pointer",
          boxSizing: "border-box",
        }}
      >
        {value.length === 0 && <span style={{ color: "var(--text-3)", fontSize: 13 }}>{fieldPlaceholder}</span>}
        {value.map((id) => (
          <span
            key={id}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 2,
              padding: "1px 2px 1px 8px",
              border: "1px solid var(--border)",
              borderRadius: 999,
              fontSize: 12,
              maxWidth: "100%",
            }}
          >
            <span className="mono" style={{ overflowWrap: "anywhere" }}>
              {id}
            </span>
            <span
              title="移除"
              role="button"
              aria-label={`移除 ${id}`}
              onClick={(event) => {
                event.stopPropagation();
                onChange(value.filter((item) => item !== id));
              }}
              style={{ cursor: "pointer", color: "var(--text-3)", padding: "2px 6px", borderRadius: 999 }}
            >
              ×
            </span>
          </span>
        ))}
      </div>
      {open && (
        <>
          <span style={{ position: "fixed", inset: 0, zIndex: 30 }} onClick={() => setOpen(false)} />
          <div
            style={{
              position: "absolute",
              top: "calc(100% + 4px)",
              left: 0,
              right: 0,
              zIndex: 31,
              background: "var(--panel)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              boxShadow: "0 12px 32px rgba(0, 0, 0, 0.18)",
              display: "grid",
            }}
          >
            <div style={{ padding: 8, borderBottom: "1px solid var(--border)" }}>
              <input
                className="input"
                autoFocus
                value={query}
                aria-label={inputLabel}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") commitInput();
                }}
                placeholder={inputPlaceholder}
                style={{ height: 30, fontSize: 12.5 }}
              />
            </div>
            <div style={{ maxHeight: 240, overflowY: "auto", padding: 6, display: "grid", gap: 2 }}>
              {options === null && <span style={MODEL_HINT_STYLE}>{loadingText}</span>}
              {options !== null && options.length === 0 && (
                <span style={MODEL_HINT_STYLE}>{failed ? failedText : emptyText}</span>
              )}
              {options !== null && matches.length === 0 && !hint && <span style={MODEL_HINT_STYLE}>{noMatchText}</span>}
              {hint && <span style={MODEL_HINT_STYLE}>{hint}</span>}
              {matches.map((item) => {
                const picked = selected.has(item.id);
                return (
                  <div
                    key={item.id}
                    className="model-opt"
                    onClick={() => toggle(item.id)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      padding: "6px 8px",
                      borderRadius: 6,
                      cursor: "pointer",
                    }}
                  >
                    <span
                      aria-hidden
                      style={{
                        width: 16,
                        height: 16,
                        borderRadius: 4,
                        border: `1px solid ${picked ? "var(--accent)" : "var(--border-strong)"}`,
                        background: picked ? "var(--accent)" : "transparent",
                        color: picked ? "var(--accent-contrast)" : "transparent",
                        display: "grid",
                        placeItems: "center",
                        flexShrink: 0,
                      }}
                    >
                      <IconCheck size={11} />
                    </span>
                    <span className="mono" style={{ fontSize: 12.5, overflowWrap: "anywhere" }}>
                      {item.id}
                    </span>
                    {item.note && (
                      <span
                        style={{
                          marginLeft: "auto",
                          fontSize: 12,
                          color: "var(--text-3)",
                          whiteSpace: "nowrap",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                        }}
                      >
                        {item.note}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </>
      )}
    </span>
  );
}

/** 目标模型多选：搜索勾选 models.dev 官方目录，目录外的名字输入后回车添加；
 *  已选清单与下拉同序（同厂商的模型挨在一起，厂商内发布日期倒序、同日期按名称），
 *  目录外自定义的垫底。 */
function ModelMultiSelect({ value, onChange }: { value: string[]; onChange: (next: string[]) => void }) {
  const [options, setOptions] = useState<MultiOption[] | null>(null);
  // 模型 id → 厂商顺序号与发布日期：已选清单按与下拉同款规则排序时查用
  const [orderById, setOrderById] = useState<ReadonlyMap<string, { vendorRank: number; release: string }> | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    apiSend<CatalogData>("/api/catalog", "GET")
      .then((data) => {
        if (cancelled) return;
        const rows = Object.values(data.models ?? {})
          .filter((entry) => typeof entry.model === "string" && entry.model)
          .map((entry) => ({
            id: entry.model,
            vendor: typeof entry.vendor === "string" ? entry.vendor : "",
            // 发布日期倒序：新模型排前面；没标日期的垫在本厂商后部
            release: typeof entry.release_date === "string" ? entry.release_date : "",
            note: [entry.name ?? "", entry.vendor ?? ""].filter(Boolean).join(" · ") || undefined,
          }));
        // 厂商顺序 = 目录里厂商首次出现的顺序：同一家的模型天然挨着
        const rankByVendor = new Map<string, number>();
        const orderById = new Map<string, { vendorRank: number; release: string }>();
        for (const row of rows) {
          if (row.vendor && !rankByVendor.has(row.vendor)) rankByVendor.set(row.vendor, rankByVendor.size);
          orderById.set(row.id, { vendorRank: rankByVendor.get(row.vendor) ?? Number.MAX_SAFE_INTEGER, release: row.release });
        }
        setOrderById(orderById);
        const byGroup = (a: { id: string; release: string }, b: { id: string; release: string }) => {
          const va = orderById.get(a.id)?.vendorRank ?? Number.MAX_SAFE_INTEGER;
          const vb = orderById.get(b.id)?.vendorRank ?? Number.MAX_SAFE_INTEGER;
          return va - vb || b.release.localeCompare(a.release) || a.id.localeCompare(b.id);
        };
        setOptions(
          rows
            .map(({ id, release, note }) => ({ id, release, note }))
            .sort(byGroup)
            .map(({ id, note }) => ({ id, note })),
        );
      })
      .catch(() => {
        if (cancelled) return;
        setOptions([]);
        setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /** 已选清单按目录同款规则排序：同厂商挨在一起、厂商内发布日期倒序、同日期按名称，
   *  目录外的自定义模型垫底。显示与保存都过它：存量乱序打开页面即归位，编辑保存落库的也是同一套顺序。 */
  const sortModels = useCallback(
    (list: string[]) => {
      if (!orderById) return list;
      return list
        .map((id) => ({ id, ...(orderById.get(id) ?? { vendorRank: Number.MAX_SAFE_INTEGER, release: "" }) }))
        .sort(
          (a, b) =>
            a.vendorRank - b.vendorRank || b.release.localeCompare(a.release) || a.id.localeCompare(b.id),
        )
        .map(({ id }) => id);
    },
    [orderById],
  );
  const sortedValue = useMemo(() => sortModels(value), [sortModels, value]);

  return (
    <TagMultiSelect
      value={sortedValue}
      onChange={(next) => onChange(sortModels(next))}
      options={options}
      failed={failed}
      width="100%"
      ariaLabel="选择目标模型"
      inputLabel="搜索或添加模型"
      fieldPlaceholder="点开搜索勾选模型，自定义的也能加"
      inputPlaceholder="搜模型名或厂商；回车把输入添加为自定义模型"
      loadingText="模型目录加载中…"
      emptyText="目录是空的，直接输入模型名回车添加"
      failedText="模型目录还没同步好，直接输入模型名回车添加"
      noMatchText="没有匹配的模型；回车把当前输入添加为自定义模型"
    />
  );
}

/** 分组白名单多选：候选是站点已采集价格数据里出现过的分组名（后端去重）；
 *  新站点还没采过数据时没有候选，直接输入分组名回车添加。 */
function GroupMultiSelect({ siteId, value, onChange }: { siteId: string; value: string[]; onChange: (next: string[]) => void }) {
  const [options, setOptions] = useState<MultiOption[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    // 新站点没有已保存的 id，不请求；编辑时按保存时的站点 id 取已采集分组
    if (!siteId) {
      setOptions([]);
      return;
    }
    let cancelled = false;
    apiSend<{ groups: string[] }>(`/api/sites/${encodeURIComponent(siteId)}/groups`, "GET")
      .then((data) => {
        if (cancelled) return;
        setOptions((data.groups ?? []).filter((item) => typeof item === "string" && item).map((id) => ({ id })));
      })
      .catch(() => {
        if (cancelled) return;
        setOptions([]);
        setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [siteId]);

  return (
    <TagMultiSelect
      value={value}
      onChange={onChange}
      options={options}
      failed={failed}
      ariaLabel="选择分组白名单"
      inputLabel="搜索或添加分组"
      fieldPlaceholder="点开勾选已采集的分组，直接输入回车也能加"
      inputPlaceholder="输入过滤分组；回车把输入添加为白名单"
      loadingText="已采集分组加载中…"
      emptyText="还没有采集到分组，直接输入分组名回车添加"
      failedText="分组列表没拉到，直接输入分组名回车添加"
      noMatchText="没有匹配的分组；回车把当前输入添加为白名单"
    />
  );
}

export { GroupMultiSelect, ModelMultiSelect };
