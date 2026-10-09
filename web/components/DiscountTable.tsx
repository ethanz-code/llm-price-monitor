"use client";

import { useMemo } from "react";
import { DataTable, type DColumn } from "./DataTable";
import { RiskLink } from "./RiskLink";
import { TermTip } from "./TermTip";
import { DiscountBars, DiscountRangeBar } from "./DiscountBars";
import { getSiteInfo } from "@/lib/sites";
import { useNarrow } from "@/lib/useNarrow";
import { formatDiscount, formatPrice } from "@/lib/format";
import { dotsOfGroup, type ChannelDotRow } from "@/lib/channelStatus";
import { ChannelDotMatrix } from "./ChannelDotMatrix";
import type { DiscountData, DiscountRow, DiscountSummaryItem } from "@/lib/types";

export function DiscountTable({
  data,
  statusDots,
}: {
  data: DiscountData;
  /** 站点渠道检测点序列；缺失时渠道列显示 — */
  statusDots?: Record<string, ChannelDotRow[]>;
}) {
  const narrow = useNarrow();
  const summaryRows = useMemo(() => Object.values(data.summary), [data.summary]);
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
    { title: "对比站点数", dataIndex: "sites_compared", align: "right", width: 120, mobileHide: true },
  ];

  return (
    <div className="section-gap rise-in" style={{ display: "grid", gap: 24 }}>
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
            mobileScrollX={620}
          />
        </div>
      )}

      <div className="panel" style={{ overflow: "hidden" }}>
        <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--border)", fontWeight: 550, fontSize: 15 }}>
          逐站点明细
          <span style={{ color: "var(--text-3)", fontSize: 12.5, fontWeight: 400, marginLeft: 10 }}>
            同一模型的各站点放在一起比，绿色行就是该模型的全网最低
          </span>
        </div>
        {data.discounts.length === 0 ? (
          <div className="empty">还没有可比的折扣数据，先去管理端添加站点并跑一轮采集，再回来看看</div>
        ) : (
          <div className="dtable-wrap">
            <div className="dtable-scroll">
              {/* 窄屏隐藏「厂商定价页」列后只需站点+分组+折扣三列，收紧最小宽度避免空滚动 */}
              <table
                className="dtable"
                style={{ minWidth: narrow ? "min(510px, 100%)" : "min(1060px, 100%)" }}
              >
                <thead>
                  <tr>
                    <th style={{ width: 190 }}>站点</th>
                    <th style={{ width: 180 }}>
                      分组<TermTip term="group" />
                    </th>
                    <th style={{ minWidth: 130, textAlign: "right" }}>
                      折扣<span className="thead-unit">相对厂商价</span>
                      <TermTip term="discount" />
                    </th>
                    <th style={{ width: 150, textAlign: "right" }} className="col-hide-m">
                      站点价<span className="thead-unit">CNY / 1M tokens</span>
                      <TermTip term="unit" />
                    </th>
                    <th style={{ width: 190 }} className="col-hide-m">
                      渠道<TermTip term="channels" />
                    </th>
                    <th style={{ width: 110, textAlign: "right" }} className="col-hide-m">
                      厂商定价页
                    </th>
                  </tr>
                </thead>
                {groups.map((group) => (
                  <tbody key={group.model}>
                    <tr className="disc-group-head">
                      <td colSpan={6}>
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
                            {row.group && row.group !== "default" ? (
                              <span className="mono">{row.group}</span>
                            ) : (
                              <span style={{ color: "var(--text-3)" }}>默认</span>
                            )}
                          </td>
                          <td>
                            {/* 3 列布局下表格会撑满面板，给折扣条限宽避免被拉到整行 */}
                            <div style={{ maxWidth: 240, marginLeft: "auto" }}>
                              <DiscountBars discount={{ input: row.input, output: row.output }} />
                            </div>
                          </td>
                          <td style={{ textAlign: "right" }} className="col-hide-m">
                            {row.input_price_cny !== null || row.output_price_cny !== null ? (
                              <span className="mono">
                                ¥{formatPrice(row.input_price_cny)} / ¥{formatPrice(row.output_price_cny)}
                              </span>
                            ) : (
                              <span style={{ color: "var(--text-3)" }}>—</span>
                            )}
                          </td>
                          <td className="col-hide-m">
                            <ChannelDotMatrix
                              dots={dotsOfGroup(statusDots?.[row.site_id], row.group ?? undefined)}
                              name={row.group ?? row.model}
                              href={`/overview/status/${encodeURIComponent(row.site_id)}`}
                            />
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
