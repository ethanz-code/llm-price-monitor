"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { DataTable, type DColumn } from "./DataTable";
import { Empty } from "./ui";
import { IconMonitor } from "./icons";
import { ToneTag, RulePriceMark } from "./ToneTag";
import { RiskLink } from "./RiskLink";
import { TermTip } from "./TermTip";
import { getSiteInfo } from "@/lib/sites";
import { effectiveCnyPrice, formatDiscount, formatTime, noticeExcerpt, recordStatusKey, rowReason, statusMeta } from "@/lib/format";
import { canonicalModel, hasUsablePrice, smartOrderRows } from "@/lib/priceRows";
import { dotsOfGroup, dotsSuccessRate, type ChannelDot, type ChannelDotRow } from "@/lib/channelStatus";
import { ChannelDotMatrix } from "./ChannelDotMatrix";
import { DiscountBars } from "./DiscountBars";
import { PriceCell } from "./PriceCell";
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

export function OverviewTable({ data, statusDots }: { data: OverviewData; statusDots?: Record<string, ChannelDotRow[]> }) {
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

  // 默认行序（智能排序）：综合价（输入 3 : 输出 1）低的在前、扣分少的在前；点表头排序循环回"无排序"即回到此序
  const parentRows = useMemo(
    () =>
      smartOrderRows(
        active?.rows ?? [],
        (row) => {
          // 缺信息扣分：渠道按本行分组的成功率扣 0–3 分，没配置渠道状态、没有公告各扣 1；
          // 站点配置信息填得越少扣得越多（每缺一项 0.5 分，最多 1.5 分）
          const success = dotsSuccessRate(dotsByRowKey.get(rowKeyOf(row)));
          let penalty = 0;
          if (success == null) penalty += 1;
          else penalty += (1 - success) * 3;
          if (!data.notices?.[row.site_id]?.content) penalty += 1;
          const filled = data.site_completeness?.[row.site_id] ?? 0;
          penalty += (3 - Math.min(3, Math.max(0, filled))) * 0.5;
          // 数据滞后扣分：价格是沿用上次快照的（last_price_at 落后 captured_at），多半是 token 过期
          // 没取到新数据；滞后超过 1 天开始扣，每多滞后一天多扣 1 分，最多 3 分，把它往后排
          const staleSeconds = row.captured_at - (row.last_price_at ?? row.captured_at);
          penalty += Math.min(3, Math.max(0, staleSeconds / 86400 - 1));
          return penalty;
        },
        rate,
      ),
    [active, dotsByRowKey, rate, data.notices, data.site_completeness],
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
      width: 150,
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
      // 不设固定宽度：剩余空间都给公告；最多 3 行，超出 CSS 钳制，悬停看更长摘要
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
      {models.length > 0 && (
        <div className="rise-in" style={{ overflowX: "auto", marginBottom: 16 }}>
          <span className="seg" role="tablist">
            {models.map((group) => (
              <button
                key={group.model}
                type="button"
                role="tab"
                aria-selected={group.key === active?.key}
                className={`seg-item${group.key === active?.key ? " on" : ""}`}
                title={group.aliases.length > 0 ? `合并了不同写法：${[group.model, ...group.aliases].join("、")}` : undefined}
                onClick={() => setActiveModel(group.key)}
              >
                <span className="mono">{group.model}</span>
                <span style={{ color: "var(--text-3)", marginLeft: 6, fontSize: 12 }}>{group.sites}</span>
              </button>
            ))}
          </span>
        </div>
      )}
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
          <span style={{ fontWeight: 550, fontSize: 15 }}>
            {active ? <span className="mono">{active.model}</span> : "最新快照"}
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
              icon={<IconMonitor size={18} />}
              title="还没有价格数据"
              description="完成一轮采集后，这里会展示各站点的最新快照。"
            />
          }
        />
      </div>
    </>
  );
}
