"use client";

import { useMemo } from "react";
import { DataTable, type DColumn } from "./DataTable";
import { StatCard } from "./PageHeader";
import { RiskLink } from "./RiskLink";
import { TermTip } from "./TermTip";
import { DiscountBars, DiscountRangeBar } from "./DiscountBars";
import { getSiteInfo } from "@/lib/sites";
import { formatDiscount, formatPrice } from "@/lib/format";
import type { DiscountData, DiscountRow, DiscountSummaryItem } from "@/lib/types";

export function DiscountTable({ data }: { data: DiscountData }) {
  const summaryRows = useMemo(() => Object.values(data.summary), [data.summary]);
  const best = useMemo(() => {
    const inputs = data.discounts.map((d) => d.input).filter((v): v is number => v !== null);
    return inputs.length ? Math.min(...inputs) : null;
  }, [data.discounts]);

  // 明细按模型分组：厂商价同模型完全一致，收进组头只出现一次，消除逐行重复
  const groups = useMemo(() => {
    const byModel = new Map<string, DiscountRow[]>();
    for (const row of data.discounts) {
      const list = byModel.get(row.model);
      if (list) list.push(row);
      else byModel.set(row.model, [row]);
    }
    const entries = [...byModel.entries()].map(([model, rows]) => ({
      model,
      // 组内按输入折扣升序，无折扣的沉底；首行即该模型全网最低，渲染时高亮
      rows: [...rows].sort((a, b) => (a.input ?? 9) - (b.input ?? 9)),
    }));
    // 组间也按最优折扣升序：更便宜的模型排前面
    return entries.sort((a, b) => (a.rows[0]?.input ?? 9) - (b.rows[0]?.input ?? 9));
  }, [data.discounts]);

  const summaryColumns: DColumn<DiscountSummaryItem>[] = [
    { title: "模型", dataIndex: "model", width: 220, render: (v: string) => <span className="mono" style={{ fontWeight: 550 }}>{v}</span> },
    {
      title: (
        <>
          输入折扣<TermTip term="discount_input" />
          <span className="thead-unit thead-unit-block">均值（最低–最高）</span>
        </>
      ),
      dataIndex: "input_discount",
      key: "input_avg",
      align: "right",
      width: 185,
      sorter: (a, b) => a.input_discount.avg - b.input_discount.avg,
      render: (_, row) => (
        <span style={{ display: "inline-grid", justifyItems: "end", gap: 4 }}>
          <span className="mono num" style={{ fontWeight: 550 }}>{formatDiscount(row.input_discount.avg)}</span>
          <DiscountRangeBar min={row.input_discount.min} max={row.input_discount.max} avg={row.input_discount.avg} />
          <span className="mono" style={{ color: "var(--text-3)", fontSize: 12 }}>
            {formatDiscount(row.input_discount.min)} – {formatDiscount(row.input_discount.max)}
          </span>
        </span>
      ),
    },
    {
      title: (
        <>
          输出折扣<TermTip term="discount_output" />
          <span className="thead-unit thead-unit-block">均值（最低–最高）</span>
        </>
      ),
      dataIndex: "output_discount",
      key: "output_avg",
      align: "right",
      width: 185,
      sorter: (a, b) => a.output_discount.avg - b.output_discount.avg,
      render: (_, row) => (
        <span style={{ display: "inline-grid", justifyItems: "end", gap: 4 }}>
          <span className="mono num" style={{ fontWeight: 550 }}>{formatDiscount(row.output_discount.avg)}</span>
          <DiscountRangeBar min={row.output_discount.min} max={row.output_discount.max} avg={row.output_discount.avg} />
          <span className="mono" style={{ color: "var(--text-3)", fontSize: 12 }}>
            {formatDiscount(row.output_discount.min)} – {formatDiscount(row.output_discount.max)}
          </span>
        </span>
      ),
    },
    { title: "对比站点数", dataIndex: "sites_compared", align: "right", width: 120 },
  ];

  return (
    <div className="section-gap rise-in" style={{ display: "grid", gap: 24 }}>
      <div className="stat-grid">
        <StatCard label="对比条目" value={data.discounts.length} hint={`${data.skipped.length} 条暂缺可比厂商价`} />
        <StatCard
          label={
            <>
              最低输入折扣
              <TermTip term="discount_input" />
            </>
          }
          value={best !== null ? formatDiscount(best) : "—"}
          hint="全网最便宜的站点价"
        />
        <StatCard label="覆盖模型" value={summaryRows.length} hint="至少一个站点有可用价" />
        <StatCard
          label={
            <>
              汇率 USD/CNY
              <TermTip term="rate" />
            </>
          }
          value={data.usd_cny_rate}
          hint={data.rate_source}
        />
      </div>

      {summaryRows.length > 0 && (
        <div className="panel" style={{ overflow: "hidden" }}>
          <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--border)", fontWeight: 550, fontSize: 15 }}>
            按模型汇总
          </div>
          <DataTable<DiscountSummaryItem>
            rowKey="model"
            columns={summaryColumns}
            rows={summaryRows}
            scrollX={710}
          />
        </div>
      )}

      <div className="panel" style={{ overflow: "hidden" }}>
        <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--border)", fontWeight: 550, fontSize: 15 }}>
          逐站点明细
          <span style={{ color: "var(--text-3)", fontSize: 12.5, fontWeight: 400, marginLeft: 10 }}>
            按模型分组，组内按输入折扣从低到高，绿色行是该模型全网最低
          </span>
        </div>
        {data.discounts.length === 0 ? (
          <div className="empty">暂无可对比的价格</div>
        ) : (
          <div className="dtable-wrap">
            <div className="dtable-scroll">
              <table className="dtable" style={{ minWidth: "min(620px, 100%)" }}>
                <thead>
                  <tr>
                    <th style={{ width: 150 }}>站点</th>
                    <th>
                      折扣<span className="thead-unit">相对厂商价</span>
                      <TermTip term="discount" />
                    </th>
                    <th style={{ width: 110, textAlign: "right" }} className="col-hide-m">
                      厂商定价页
                    </th>
                  </tr>
                </thead>
                {groups.map((group) => (
                  <tbody key={group.model}>
                    <tr className="disc-group-head">
                      <td colSpan={3}>
                        <span className="disc-group-inner">
                          <span className="mono disc-group-model">{group.model}</span>
                          <span className="mono disc-group-price">
                            厂商价 ¥{formatPrice(group.rows[0].official_input_cny)} / ¥
                            {formatPrice(group.rows[0].official_output_cny)}
                          </span>
                        </span>
                      </td>
                    </tr>
                    {group.rows.map((row, index) => {
                      const site = getSiteInfo(row.site_id, row.source_url);
                      return (
                        <tr key={`${row.site_id}:${row.group ?? ""}`} className={index === 0 ? "row-best" : undefined}>
                          <td>
                            <RiskLink href={site.homepage || row.source_url} variant="site">
                              <span className="mono">{site.name}</span>
                            </RiskLink>
                          </td>
                          <td>
                            {/* 3 列布局下表格会撑满面板，给折扣条限宽避免被拉到整行 */}
                            <div style={{ maxWidth: 320 }}>
                              <DiscountBars discount={{ input: row.input, output: row.output }} />
                            </div>
                          </td>
                          <td style={{ textAlign: "right" }} className="col-hide-m">
                            {row.source_url ? (
                              <RiskLink href={row.source_url}>来源</RiskLink>
                            ) : (
                              <span style={{ color: "var(--text-3)" }}>—</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                ))}
              </table>
            </div>
          </div>
        )}
      </div>

      {data.skipped.length > 0 && (
        <p style={{ color: "var(--text-3)", fontSize: 13, margin: 0 }}>
          未纳入对比 {data.skipped.length} 条：
          {data.skipped
            .slice(0, 8)
            .map((item) => `${item.site_id ?? "?"}/${item.model ?? "?"}（${item.reason}）`)
            .join("、")}
          {data.skipped.length > 8 ? " 等" : ""}
        </p>
      )}
    </div>
  );
}
