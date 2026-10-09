"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { DataTable, type DColumn } from "./DataTable";
import { StatCard } from "./PageHeader";
import { Empty } from "./ui";
import { IconMonitor } from "./icons";
import { ToneTag } from "./ToneTag";
import { RiskLink } from "./RiskLink";
import { TermTip } from "./TermTip";
import { getSiteInfo } from "@/lib/sites";
import { effectiveCnyPrice, formatDiscount, formatTime, noticeExcerpt, recordStatusKey, rowReason, statusMeta } from "@/lib/format";
import { canonicalModel, hasUsablePrice, mergeModelRows, modelRowKey } from "@/lib/priceRows";
import { filterDotsByGroups, type ChannelDotRow } from "@/lib/channelStatus";
import { DiscountBars } from "./DiscountBars";
import { PriceCell } from "./PriceCell";
import type { OverviewData, OverviewRecord, SiteStatus } from "@/lib/types";

// 渠道点阵：每行是一个渠道/分组（异常在前），取该渠道最近 15 次检测的小点；行首为渠道/分组名
const CHANNEL_DOT_COLS = 15;

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

  // 表内仍沿用"同站点同分组合并为一行"的口径：代表行取最低价，其余展开可见
  const { parentRows, childRowsOf } = useMemo(
    () =>
      active
        ? mergeModelRows(active.rows)
        : { parentRows: [] as OverviewRecord[], childRowsOf: new Map<string, OverviewRecord[]>() },
    [active],
  );

  // 渠道点阵按合并组过滤：父行只显示该站点+模型涉及的分组（父+子记录的分组并集）；
  // 目标分组为空或一行都匹配不上（渠道形态接口，行名不是分组名）时不过滤
  const dotsByRowKey = useMemo(() => {
    const result = new Map<string, ChannelDotRow[]>();
    const mergeInfo = new Map<string, { siteId: string; groups: Set<string> }>();
    for (const row of active?.rows ?? []) {
      const key = modelRowKey(row);
      let info = mergeInfo.get(key);
      if (!info) {
        info = { siteId: row.site_id, groups: new Set() };
        mergeInfo.set(key, info);
      }
      const group = row.metadata?.group;
      if (group) info.groups.add(group);
    }
    for (const [key, { siteId, groups }] of mergeInfo) {
      const dots = statusDots?.[siteId];
      if (dots?.length) result.set(key, filterDotsByGroups(dots, groups));
    }
    return result;
  }, [active, statusDots]);

  const attentionSites = useMemo(() => {
    return Object.entries(data.collect_status ?? {})
      .filter(([, status]) => status.status === "error" || status.status === "auth_required")
      .map(([siteId, status]) => ({ siteId, ...status }) satisfies { siteId: string } & SiteStatus);
  }, [data.collect_status]);

  const stats = useMemo(() => {
    const sites = new Set(records.map((row) => row.site_id));
    const modelsCount = new Set(records.map((row) => row.model));
    const latest = records.reduce<number | undefined>(
      (acc, row) => (acc === undefined || row.captured_at > acc ? row.captured_at : acc),
      undefined,
    );
    const inputs = records.map((row) => row.discount?.input).filter((v): v is number => v !== null && v !== undefined);
    const avgInput = inputs.length ? inputs.reduce((a, b) => a + b, 0) / inputs.length : null;
    return { sites: sites.size, models: modelsCount.size, latest, avgInput };
  }, [records]);

  const columns: DColumn<OverviewRecord>[] = [
    {
      title: "站点",
      dataIndex: "site_id",
      width: 170,
      render: (v: string, row) => {
        const site = getSiteInfo(v, row.source_url);
        // 状态列已去掉：正常态是噪音，只有异常（需认证/无数据）才在名称旁挂标签
        const statusKey = recordStatusKey(row);
        const abnormal = statusKey === "auth_required" || statusKey === "unavailable";
        const meta = statusMeta(statusKey);
        const reason = rowReason(row);
        const tip = [reason, row.last_price_at != null ? `上次拿到数据：${formatTime(row.last_price_at)}` : null]
          .filter(Boolean)
          .join("\n") || undefined;
        return (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6, minWidth: 0, flexWrap: "wrap" }}>
            <RiskLink href={site.homepage || row.source_url} variant="site">
              <span className="mono">{site.name}</span>
            </RiskLink>
            {abnormal && (
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
      width: 140,
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
      width: 140,
      sorter: (a, b) => (effectiveCnyPrice(a, "output_price", rate) ?? -1) - (effectiveCnyPrice(b, "output_price", rate) ?? -1),
      render: (_v: number | null, row) => <PriceCell row={row} field="output_price" rate={rate} />,
    },
    {
      title: "分组",
      key: "group",
      width: 140,
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
      width: 180,
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
      width: 250,
      mobileHide: true,
      render: (_v: unknown, row) => {
        const key = modelRowKey(row);
        // 折叠子行不重复渲染点阵，渠道状态只在父行显示
        if (childRowsOf.get(key)?.includes(row)) return <span style={{ color: "var(--text-3)" }}>—</span>;
        const rows = dotsByRowKey.get(key) ?? [];
        if (rows.length === 0) return <span style={{ color: "var(--text-3)" }}>—</span>;
        return (
          <Link href={`/overview/status/${encodeURIComponent(row.site_id)}`} className="ch-matrix" title="每行是一个渠道/分组的最近检测（异常在前），点击查看趋势图">
            {rows.map((channel) => (
              <span key={channel.name} className="ch-line-dots">
                <span className="ch-line-name" title={channel.name}>
                  {channel.name}
                </span>
                {channel.dots.slice(-CHANNEL_DOT_COLS).map((dot, index) => (
                  <span
                    key={index}
                    aria-hidden
                    title={`${channel.name}：${dot.status}${dot.at != null ? ` · ${formatTime(dot.at)}` : ""}`}
                    className={`ch-mini ${dot.ok ? "ch-ok" : "ch-down"}`}
                  />
                ))}
              </span>
            ))}
          </Link>
        );
      },
    },
    {
      title: "公告",
      key: "notice",
      // 不设固定宽度：剩余空间都给公告摘要，窄窗口时靠省略号收敛
      mobileHide: true,
      ellipsis: true,
      render: (_v: unknown, row) => {
        const notice = data.notices?.[row.site_id];
        if (!notice?.content) return <span style={{ color: "var(--text-3)" }}>—</span>;
        const tip = [
          noticeExcerpt(notice.content),
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
            {noticeExcerpt(notice.content)}
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
                  {site.error || meta.label}
                </span>
              </span>
            );
          })}
        </div>
      )}
      <div className="stat-grid rise-in" style={{ marginBottom: 20 }}>
        <StatCard tone="blue" label="监控站点" value={stats.sites} hint="按配置批量采集" />
        <StatCard tone="gray" label="跟踪模型" value={stats.models} hint={`${records.length} 条价格记录`} />
        <StatCard
          tone="yellow"
          label="最近采集"
          value={stats.latest ? formatTime(stats.latest).slice(0, 10) : "—"}
          hint={stats.latest ? formatTime(stats.latest).slice(11) : undefined}
        />
        <StatCard
          tone="green"
          label={
            <>
              平均输入折扣
              <TermTip term="discount_input" />
            </>
          }
          value={stats.avgInput !== null ? formatDiscount(stats.avgInput) : "—"}
          hint="相对厂商原价"
        />
      </div>
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
          <span style={{ fontWeight: 550, fontSize: 15, display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
            {active ? (
              <>
                <span className="mono">{active.model}</span>
                {active.aliases.length > 0 && (
                  <span style={{ color: "var(--text-3)", fontSize: 12.5, fontWeight: 400 }}>
                    别名：{active.aliases.join("、")}
                  </span>
                )}
              </>
            ) : (
              "最新快照"
            )}
            {active && (
              <span style={{ color: "var(--text-3)", fontSize: 12.5, fontWeight: 400 }}>
                {active.rows.length} 条记录 · {active.sites} 个站点 · 同站点同分组合并取最低价，点名称右侧 +N 展开其余写法，点行内其他位置查看站点检测详情
              </span>
            )}
          </span>
          {data.catalog.enabled && (
            <span style={{ color: "var(--text-2)", fontSize: 13 }}>
              厂商价快照 <span className="mono">{data.catalog.generated_at_iso ?? "—"}</span> · 汇率{" "}
              <span className="mono">{data.catalog.usd_cny_rate ?? "—"}</span>（{data.catalog.rate_source}）·{" "}
              <Link href="/discount" style={{ color: "var(--accent-text)" }}>
                折扣明细
              </Link>
            </span>
          )}
        </div>
        <DataTable<OverviewRecord>
          rowKey={(row) => `${row.site_id}:${row.model}:${row.metadata?.group ?? ""}`}
          columns={columns}
          rows={parentRows}
          childrenOf={(row) => childRowsOf.get(modelRowKey(row))}
          paginated
          scrollX={1020}
          mobileScrollX={500}
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
