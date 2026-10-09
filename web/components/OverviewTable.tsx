"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { DataTable, type DColumn } from "./DataTable";
import { Empty, Input } from "./ui";
import { IconChevronDown, IconSearch } from "./icons";
import { DajuSit } from "./DajuArt";
import { ToneTag, RulePriceMark } from "./ToneTag";
import { RiskLink } from "./RiskLink";
import { TermTip } from "./TermTip";
import { getSiteInfo } from "@/lib/sites";
import { effectiveCnyCachePrice, effectiveCnyPrice, formatDiscount, formatTime, noticeExcerpt, recordStatusKey, rowReason, statusMeta } from "@/lib/format";
import { canonicalModel, hasUsablePrice, smartOrderRows } from "@/lib/priceRows";
import { rankingHit, type RankingHit } from "@/lib/rankings";
import { dotsOfGroup, dotsSuccessRate, type ChannelDot, type ChannelDotRow } from "@/lib/channelStatus";
import { ChannelDotMatrix } from "./ChannelDotMatrix";
import { DiscountBars } from "./DiscountBars";
import { PriceCell, CachePriceCell } from "./PriceCell";
import type { OverviewData, OverviewRecord, SiteStatus } from "@/lib/types";

/** 行唯一键：同站点同模型不再折叠，不同分组/单位各占一行。 */
const rowKeyOf = (row: OverviewRecord) =>
  `${row.site_id}:${row.model}:${row.unit}:${row.metadata?.group ?? ""}`;

interface ModelGroup {
  /** 归一后的分组键：大小写/连字符等写法差异不产生重复的表 */
  key: string;
  /** 展示名：该组内出现次数最多的写法 */
  model: string;
  /** 其余写法（语义相同、字符不同），在表头标注 */
  aliases: string[];
  rows: OverviewRecord[];
  sites: number;
  priced: boolean;
}

export function OverviewTable({
  data,
  statusDots,
  rankingsIndex = {},
}: {
  data: OverviewData;
  statusDots?: Record<string, ChannelDotRow[]>;
  /** AA 榜单匹配索引：给当前选中模型挂排名徽标；榜单未生成时缺省不显示 */
  rankingsIndex?: Record<string, RankingHit>;
}) {
  const records = data.records;
  const router = useRouter();
  // 站点价统一按 RMB 展示与排序：USD 记录乘厂商价快照汇率，CNY 记录原样；无汇率回落原币
  const rate = data.catalog.usd_cny_rate ?? null;

  // 按模型拆表：一个模型一张表；默认选中排序后的第一个，用户切换后跟随其选择
  const [activeModel, setActiveModel] = useState<string | null>(null);

  const models = useMemo<ModelGroup[]>(() => {
    const byModel = new Map<string, { rows: OverviewRecord[]; names: Map<string, number> }>();
    for (const row of records) {
      const key = canonicalModel(row.model);
      let entry = byModel.get(key);
      if (!entry) {
        entry = { rows: [], names: new Map() };
        byModel.set(key, entry);
      }
      entry.rows.push(row);
      entry.names.set(row.model, (entry.names.get(row.model) ?? 0) + 1);
    }
    const groups = [...byModel.entries()].map(([key, entry]) => {
      // 展示名取出现最多的一种写法，其余写法记为别名
      const ranked = [...entry.names.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
      return {
        key,
        model: ranked[0][0],
        aliases: ranked.slice(1).map(([name]) => name),
        rows: entry.rows,
        sites: new Set(entry.rows.map((row) => row.site_id)).size,
        priced: entry.rows.some(hasUsablePrice),
      };
    });
    // 有真实价的模型在前；同状态按覆盖站点数多的在前，名称稳定排序兜底
    groups.sort((a, b) =>
      a.priced !== b.priced ? (a.priced ? -1 : 1) : b.sites - a.sites || a.model.localeCompare(b.model),
    );
    return groups;
  }, [records]);

  const active = models.find((group) => group.key === activeModel) ?? models[0];

  // 渠道点阵按行取数：只认与本行分组名对得上的渠道行，不做全量回退；
  // 没有分组或匹配不上（渠道形态接口，行名不是分组名）时不显示点阵
  const dotsByRowKey = useMemo(() => {
    const result = new Map<string, ChannelDot[]>();
    for (const row of active?.rows ?? []) {
      const dots = dotsOfGroup(statusDots?.[row.site_id], row.metadata?.group);
      if (dots) result.set(rowKeyOf(row), dots);
    }
    return result;
  }, [active, statusDots]);

  // 默认行序（智能排序）：可靠性加权综合价低的在前——综合价（输入 3 : 输出 1）乘可靠性系数，
  // 扣分只看渠道健康与数据滞后，满档虚增 20%；点表头排序循环回"无排序"即回到此序
  const parentRows = useMemo(
    () =>
      smartOrderRows(
        active?.rows ?? [],
        (row) => {
          // 渠道按本行分组的成功率扣 0–3 分；没配置渠道状态扣 1（没监测=不知道好坏，轻微不可信）
          const success = dotsSuccessRate(dotsByRowKey.get(rowKeyOf(row)));
          let penalty = 0;
          if (success == null) penalty += 1;
          else penalty += (1 - success) * 3;
          // 数据滞后扣分：价格是沿用上次快照的（last_price_at 落后 captured_at），多半是 token 过期
          // 没取到新数据；滞后超过 1 天开始扣，每多滞后一天多扣 1 分，最多 3 分
          const staleSeconds = row.captured_at - (row.last_price_at ?? row.captured_at);
          penalty += Math.min(3, Math.max(0, staleSeconds / 86400 - 1));
          // 满分 6 分（渠道 3 + 滞后 3）归一到 0–1，乘 20% 上限后满档让综合价虚增 20%
          return penalty / 6;
        },
        rate,
      ),
    [active, dotsByRowKey, rate],
  );

  const attentionSites = useMemo(() => {
    return Object.entries(data.collect_status ?? {})
      .filter(([, status]) => status.status === "error" || status.status === "auth_required")
      .map(([siteId, status]) => ({ siteId, ...status }) satisfies { siteId: string } & SiteStatus);
  }, [data.collect_status]);

  const columns: DColumn<OverviewRecord>[] = [
    {
      title: "站点",
      dataIndex: "site_id",
      // 不设固定宽度：与公告列一起平分剩余空间（两者都不设 width 即均分），
      // 避免公告在宽屏独吞剩余宽度、也避免窄屏挤压价格列；手机端由 mobileScrollX 兜底
      render: (v: string, row) => {
        const site = getSiteInfo(v, row.source_url);
        // 状态列已去掉：确认/候选价是常态不挂标签；需认证/无数据挂标签，规则价用小字低调标注
        const statusKey = recordStatusKey(row);
        const isRule = statusKey === "rule_only";
        const showTag = statusKey === "auth_required" || statusKey === "unavailable";
        const meta = statusMeta(statusKey);
        const reason =
          isRule
            ? "价格由 AI 从站点数据推算，未经页面交叉验证，仅供参考"
            : rowReason(row);
        const tip = [reason, row.last_price_at != null ? `上次拿到数据：${formatTime(row.last_price_at)}` : null]
          .filter(Boolean)
          .join("\n") || undefined;
        return (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6, minWidth: 0, flexWrap: "wrap" }}>
            <RiskLink href={site.homepage || row.source_url} variant="site">
              <span className="mono">{site.name}</span>
            </RiskLink>
            {isRule && <RulePriceMark tip={tip} />}
            {showTag && (
              <span title={tip}>
                <ToneTag tone={meta.tone}>{meta.label}</ToneTag>
              </span>
            )}
          </span>
        );
      },
      sorter: (a, b) => a.site_id.localeCompare(b.site_id),
    },
    {
      title: (
        <>
          输入价<TermTip term="input_price" />
          <span className="thead-unit thead-unit-block">CNY / 1M tokens</span>
        </>
      ),
      dataIndex: "input_price",
      align: "right",
      width: 120,
      sorter: (a, b) => (effectiveCnyPrice(a, "input_price", rate) ?? -1) - (effectiveCnyPrice(b, "input_price", rate) ?? -1),
      render: (_v: number | null, row) => <PriceCell row={row} field="input_price" rate={rate} />,
    },
    {
      title: (
        <>
          输出价<TermTip term="output_price" />
          <span className="thead-unit thead-unit-block">CNY / 1M tokens</span>
        </>
      ),
      dataIndex: "output_price",
      align: "right",
      width: 120,
      sorter: (a, b) => (effectiveCnyPrice(a, "output_price", rate) ?? -1) - (effectiveCnyPrice(b, "output_price", rate) ?? -1),
      render: (_v: number | null, row) => <PriceCell row={row} field="output_price" rate={rate} />,
    },
    {
      title: (
        <>
          缓存价<TermTip term="cache_price" />
          <span className="thead-unit thead-unit-block">CNY / 1M tokens</span>
        </>
      ),
      key: "cache_price",
      align: "right",
      width: 120,
      mobileHide: true,
      sorter: (a, b) =>
        (effectiveCnyCachePrice(a, "cache_read_price", rate) ?? -1) -
        (effectiveCnyCachePrice(b, "cache_read_price", rate) ?? -1),
      render: (_v: unknown, row) => <CachePriceCell row={row} rate={rate} />,
    },
    {
      title: "分组",
      key: "group",
      width: 110,
      mobileHide: true,
      render: (_v: unknown, row) => {
        const group = row.metadata?.group;
        return group ? (
          <span className="mono" style={{ fontSize: 12.5, color: "var(--text-2)" }}>
            {group}
          </span>
        ) : (
          <span style={{ color: "var(--text-3)" }}>—</span>
        );
      },
    },
    {
      title: (
        <>
          厂商价折扣
          <TermTip term="discount" />
        </>
      ),
      key: "discount",
      width: 150,
      mobileHide: true,
      sorter: (a, b) => (a.discount?.input ?? 9) - (b.discount?.input ?? 9),
      render: (_, row) => <DiscountBars discount={row.discount} />,
    },
    {
      title: (
        <>
          渠道
          <TermTip term="channels" />
        </>
      ),
      key: "channels",
      width: 200,
      mobileHide: true,
      render: (_v: unknown, row) => {
        const dots = dotsByRowKey.get(rowKeyOf(row));
        return (
          <ChannelDotMatrix
            dots={dots}
            name={row.metadata?.group ?? row.model}
            href={`/overview/status/${encodeURIComponent(row.site_id)}`}
          />
        );
      },
    },
    {
      title: "公告",
      key: "notice",
      // 不设固定宽度：与站点列平分剩余空间，宽屏下不再独吞；最多 2 行，悬停看更长摘要
      mobileHide: true,
      render: (_v: unknown, row) => {
        const notice = data.notices?.[row.site_id];
        if (!notice?.content) return <span style={{ color: "var(--text-3)" }}>—</span>;
        const tip = [
          noticeExcerpt(notice.content, 6),
          notice.captured_at ? `发布于 ${formatTime(notice.captured_at)}` : null,
        ]
          .filter(Boolean)
          .join("\n");
        return (
          <Link
            href={`/overview/status/${encodeURIComponent(row.site_id)}`}
            title={tip}
            style={{ color: "var(--text-2)" }}
          >
            <span
              style={{
                display: "-webkit-box",
                WebkitLineClamp: 2,
                WebkitBoxOrient: "vertical",
                overflow: "hidden",
                whiteSpace: "normal",
                lineHeight: 1.55,
              }}
            >
              {noticeExcerpt(notice.content, 2)}
            </span>
          </Link>
        );
      },
    },
  ];

  return (
    <>
      {attentionSites.length > 0 && (
        <div
          className="alert alert-warn alert-band rise-in"
          style={{ marginBottom: 24, display: "flex", flexWrap: "wrap", gap: "8px 18px", alignItems: "center" }}
        >
          <span style={{ fontSize: 13.5, fontWeight: 550 }}>需要关注的站点：</span>
          {attentionSites.map((site) => {
            const meta = statusMeta(site.status);
            return (
              <span key={site.siteId} title={site.error ?? undefined} style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                <ToneTag tone={meta.tone}>{getSiteInfo(site.siteId).name || site.siteId}</ToneTag>
                <span className="mono" style={{ color: "var(--text-2)", fontSize: 12.5 }}>
                  {meta.label}
                </span>
              </span>
            );
          })}
        </div>
      )}
      {models.length > 0 && <ModelPicker models={models} activeKey={active?.key} onSelect={setActiveModel} />}
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
          <span style={{ display: "inline-flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
            <span style={{ fontWeight: 550, fontSize: 15 }}>
              {active ? <span className="mono">{active.model}</span> : "最新快照"}
            </span>
            {active && (() => {
              const hit = rankingHit(rankingsIndex, active.model);
              if (!hit) return null;
              return (
                <span
                  className="mono"
                  title="Artificial Analysis 榜单排名与智能指数（自测口径，非本站评测）"
                  style={{ fontSize: 12, color: "var(--text-3)" }}
                >
                  Artificial Analysis #{hit.rank}
                  {hit.intelligence_index != null && ` · 指数 ${hit.intelligence_index}`}
                </span>
              );
            })()}
          </span>
          {data.catalog.enabled && (
            <span style={{ color: "var(--text-2)", fontSize: 13 }}>
              数据时间 <span className="mono">{data.catalog.generated_at_iso ?? "—"}</span> · 汇率{" "}
              <span className="mono">{data.catalog.usd_cny_rate ?? "—"}</span>
            </span>
          )}
        </div>
        <DataTable<OverviewRecord>
          rowKey={rowKeyOf}
          columns={columns}
          rows={parentRows}
          paginated
          scrollX={870}
          mobileScrollX={390}
          dense
          onRowClick={(row) => router.push(`/overview/status/${encodeURIComponent(row.site_id)}`)}
          empty={
            <Empty
              icon={<DajuSit width={30} />}
              title="还没有价格数据"
              description="完成一轮采集后，这里会展示各站点的最新快照。"
            />
          }
        />
      </div>
    </>
  );
}

/** 模型选择下拉：可搜索、显示各模型的站点数；模型多时靠输入过滤，不再平铺一排 tab。 */
function ModelPicker({
  models,
  activeKey,
  onSelect,
}: {
  models: ModelGroup[];
  activeKey?: string;
  onSelect: (key: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const rootRef = useRef<HTMLSpanElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const active = models.find((group) => group.key === activeKey);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // 打开时把选中项滚进可视区
  useEffect(() => {
    if (!open) return;
    menuRef.current?.querySelector<HTMLElement>('[data-active="true"]')?.scrollIntoView({ block: "nearest" });
  }, [open]);

  const keyword = filter.trim().toLowerCase();
  const matched = keyword
    ? models.filter(
        (group) =>
          group.model.toLowerCase().includes(keyword) ||
          group.aliases.some((alias) => alias.toLowerCase().includes(keyword)),
      )
    : models;

  return (
    // rise-in 的 both 填充让本容器与下方 panel 同为层叠上下文，展开时必须抬高，否则菜单被表格盖住
    <div className="rise-in" style={{ marginBottom: 16, position: "relative", zIndex: open ? 70 : undefined }}>
    <span className="sel-wrap" ref={rootRef} style={{ width: 300, maxWidth: "100%" }}>
      <button
        type="button"
        className="sel pick-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {active ? (
          <span style={{ display: "inline-flex", alignItems: "baseline", gap: 8 }}>
            <span className="mono">{active.model}</span>
            <span style={{ color: "var(--text-3)", fontSize: 12 }}>{active.sites}</span>
          </span>
        ) : (
          "选择模型"
        )}
      </button>
      <IconChevronDown size={13} className={`chev${open ? " is-flip" : ""}`} />
      {open && (
        <div className="pick-menu" style={{ maxWidth: "min(360px, 90vw)" }} ref={menuRef} role="listbox">
          <div style={{ padding: "4px 4px 8px", borderBottom: "1px solid var(--border)", marginBottom: 4 }}>
            <Input
              value={filter}
              onChange={setFilter}
              placeholder="搜索模型"
              prefix={<IconSearch size={13} />}
              style={{ width: "100%" }}
            />
          </div>
          {matched.length === 0 && (
            <span style={{ display: "block", padding: "8px 10px", color: "var(--text-3)", fontSize: 13 }}>
              没有匹配的模型
            </span>
          )}
          {matched.map((group) => (
            <button
              key={group.key}
              type="button"
              role="option"
              aria-selected={group.key === activeKey}
              data-active={group.key === activeKey}
              className={`pick-item${group.key === activeKey ? " on" : ""}`}
              title={group.aliases.length > 0 ? `合并了不同写法：${[group.model, ...group.aliases].join("、")}` : undefined}
              onClick={() => {
                onSelect(group.key);
                setOpen(false);
                setFilter("");
              }}
              style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 }}
            >
              <span className="mono" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
                {group.model}
              </span>
              <span style={{ color: "var(--text-3)", fontSize: 12 }}>{group.sites}</span>
            </button>
          ))}
        </div>
      )}
    </span>
    </div>
  );
}
