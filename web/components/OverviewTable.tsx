"use client";

import { useMemo } from "react";
import Link from "next/link";
import { DataTable, type DColumn } from "./DataTable";
import { StatCard } from "./PageHeader";
import { ToneTag } from "./ToneTag";
import { RiskLink } from "./RiskLink";
import { TermTip } from "./TermTip";
import { getSiteInfo } from "@/lib/sites";
import { currencySymbol, formatDiscount, formatPrice, formatTime, recordStatusKey, rowReason, statusMeta, tieredPriceRange, TIERED_PRICE_TIP } from "@/lib/format";
import { DiscountBars } from "./DiscountBars";
import type { OverviewData, OverviewRecord, SiteStatus } from "@/lib/types";

/** 有效价 = 顶层价格或阶梯计价区间任一可得；无效行（需认证/无数据）在时间排序中沉底。 */
function hasUsablePrice(row: OverviewRecord): boolean {
  return (
    row.input_price != null ||
    row.output_price != null ||
    tieredPriceRange(row, "input_price") != null ||
    tieredPriceRange(row, "output_price") != null
  );
}

export function OverviewTable({ data }: { data: OverviewData }) {
  const records = data.records;

  // 站点+模型 合并为一行：代表行取价格最低的一条，其余记录展开后可见；单位不同的记录不并组
  const { parentRows, childRowsOf } = useMemo(() => {
    const groups = new Map<string, OverviewRecord[]>();
    for (const row of records) {
      const key = `${row.site_id}:${row.model}:${row.unit}`;
      const list = groups.get(key);
      if (list) list.push(row);
      else groups.set(key, [row]);
    }
    // 代表行：有可用价者优先；输入价最低，其次输出价最低；再同取最新
    const better = (a: OverviewRecord, b: OverviewRecord) => {
      if (hasUsablePrice(a) !== hasUsablePrice(b)) return hasUsablePrice(a);
      const inputA = a.input_price ?? Number.POSITIVE_INFINITY;
      const inputB = b.input_price ?? Number.POSITIVE_INFINITY;
      if (inputA !== inputB) return inputA < inputB;
      const outputA = a.output_price ?? Number.POSITIVE_INFINITY;
      const outputB = b.output_price ?? Number.POSITIVE_INFINITY;
      if (outputA !== outputB) return outputA < outputB;
      return a.captured_at > b.captured_at;
    };
    const parentRows: OverviewRecord[] = [];
    const childRowsOf = new Map<string, OverviewRecord[]>();
    for (const [key, list] of groups) {
      const best = list.reduce((acc, row) => (better(row, acc) ? row : acc), list[0]);
      parentRows.push(best);
      const rest = list.filter((row) => row !== best);
      if (rest.length > 0) childRowsOf.set(key, rest);
    }
    // 有效价行在前（时间倒序），需认证/无数据的行沉底
    parentRows.sort((a, b) => {
      if (hasUsablePrice(a) !== hasUsablePrice(b)) return hasUsablePrice(a) ? -1 : 1;
      return b.captured_at - a.captured_at;
    });
    return { parentRows, childRowsOf };
  }, [records]);

  const attentionSites = useMemo(() => {
    return Object.entries(data.collect_status ?? {})
      .filter(([, status]) => status.status === "error" || status.status === "auth_required")
      .map(([siteId, status]) => ({ siteId, ...status }) satisfies { siteId: string } & SiteStatus);
  }, [data.collect_status]);

  const stats = useMemo(() => {
    const sites = new Set(records.map((row) => row.site_id));
    const models = new Set(records.map((row) => row.model));
    const latest = records.reduce<number | undefined>(
      (acc, row) => (acc === undefined || row.captured_at > acc ? row.captured_at : acc),
      undefined,
    );
    const inputs = records.map((row) => row.discount?.input).filter((v): v is number => v !== null && v !== undefined);
    const avgInput = inputs.length ? inputs.reduce((a, b) => a + b, 0) / inputs.length : null;
    return { sites: sites.size, models: models.size, latest, avgInput };
  }, [records]);

  const columns: DColumn<OverviewRecord>[] = [
    {
      title: "站点",
      dataIndex: "site_id",
      width: 115,
      render: (v: string, row) => {
        const site = getSiteInfo(v, row.source_url);
        return (
          <RiskLink href={site.homepage || row.source_url} variant="site">
            <span className="mono">{site.name}</span>
          </RiskLink>
        );
      },
      sorter: (a, b) => a.site_id.localeCompare(b.site_id),
    },
    {
      title: "模型",
      dataIndex: "model",
      width: 140,
      render: (v: string, row) => {
        const rest = childRowsOf.get(`${row.site_id}:${row.model}:${row.unit}`);
        // 子行与父行同 key，会查到同一份 rest；+N 只标在父行上。
        // 模型名单行省略：不换行撑高行距，完整名悬停可见
        return (
          <span style={{ display: "flex", alignItems: "center", gap: 4, minWidth: 0 }}>
            <span className="mono cell-ellipsis" title={v}>
              {v}
            </span>
            {rest && !rest.includes(row) && (
              <span
                title={`还有 ${rest.length} 条记录，展开查看`}
                style={{ color: "var(--text-3)", fontSize: 12, flexShrink: 0 }}
              >
                +{rest.length}
              </span>
            )}
          </span>
        );
      },
      sorter: (a, b) => a.model.localeCompare(b.model),
    },
    {
      title: (
        <>
          输入价<TermTip term="input_price" />
          <span className="thead-unit thead-unit-block">USD / 1M tokens</span>
        </>
      ),
      dataIndex: "input_price",
      align: "right",
      width: 125,
      sorter: (a, b) => (a.input_price ?? -1) - (b.input_price ?? -1),
      render: (v: number | null, row) => {
        const range = tieredPriceRange(row, "input_price");
        return (
          <span className="mono num" title={range ? TIERED_PRICE_TIP : undefined}>
            {currencySymbol(row.unit)}
            {range ?? formatPrice(v)}
          </span>
        );
      },
    },
    {
      title: (
        <>
          输出价<TermTip term="output_price" />
          <span className="thead-unit thead-unit-block">USD / 1M tokens</span>
        </>
      ),
      dataIndex: "output_price",
      align: "right",
      width: 125,
      sorter: (a, b) => (a.output_price ?? -1) - (b.output_price ?? -1),
      render: (v: number | null, row) => {
        const range = tieredPriceRange(row, "output_price");
        return (
          <span className="mono num" title={range ? TIERED_PRICE_TIP : undefined}>
            {currencySymbol(row.unit)}
            {range ?? formatPrice(v)}
          </span>
        );
      },
    },
    {
      title: "分组",
      key: "group",
      width: 90,
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
          状态
          <TermTip term="status" />
        </>
      ),
      key: "status",
      width: 80,
      mobileHide: true,
      render: (_v: unknown, row) => {
        const meta = statusMeta(recordStatusKey(row));
        const reason = rowReason(row);
        return (
          <span title={reason ?? undefined}>
            <ToneTag tone={meta.tone}>{meta.label}</ToneTag>
          </span>
        );
      },
    },
    {
      title: (
        <>
          官方价折扣
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
      title: "采集时间",
      dataIndex: "captured_at",
      width: 155,
      sorter: (a, b) => a.captured_at - b.captured_at,
      mobileHide: true,
      render: (v: number) => (
        <span className="mono" style={{ color: "var(--text-2)", fontSize: 13, whiteSpace: "nowrap" }}>
          {formatTime(v)}
        </span>
      ),
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
          hint="相对厂商官方原价"
        />
      </div>
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
            最新快照
            <span style={{ color: "var(--text-3)", fontSize: 12.5, fontWeight: 400, marginLeft: 10 }}>
              同站点同模型合并为一行（取最低价），行尾箭头展开全部
            </span>
          </span>
          {data.official.enabled && (
            <span style={{ color: "var(--text-2)", fontSize: 13 }}>
              官方价快照 <span className="mono">{data.official.generated_at_iso ?? "—"}</span> · 汇率{" "}
              <span className="mono">{data.official.usd_cny_rate ?? "—"}</span>（{data.official.rate_source}）·{" "}
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
          childrenOf={(row) => childRowsOf.get(`${row.site_id}:${row.model}:${row.unit}`)}
          pageSize={20}
          scrollX={1085}
          empty="暂无价格数据，完成一轮采集后这里会展示各站点最新快照"
        />
      </div>
    </>
  );
}
