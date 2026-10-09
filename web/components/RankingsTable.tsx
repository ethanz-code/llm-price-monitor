"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { DataTable, type DColumn } from "./DataTable";
import { Btn, Empty, Input, toast } from "./ui";
import { IconSearch } from "./icons";
import { DajuSit } from "./DajuArt";
import { TermTip } from "./TermTip";
import { apiSend } from "@/lib/api";
import { formatCount, formatIsoMinute, looseIncludes } from "@/lib/format";
import type { RankingEntry, RankingsData } from "@/lib/types";

/** AA 榜单表：Artificial Analysis 的模型自测排名，独立于站点折扣口径，仅供选型参考。 */
export function RankingsTable({ data }: { data: RankingsData | null }) {
  const [keyword, setKeyword] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const router = useRouter();

  const rows = useMemo<RankingEntry[]>(() => {
    const all = data?.models ?? [];
    if (!keyword.trim()) return all;
    return all.filter(
      (row) => looseIncludes(row.name, keyword) || looseIncludes(row.creator ?? "", keyword) || looseIncludes(row.slug, keyword),
    );
  }, [data, keyword]);

  /** 手动触发榜单同步（需管理员登录）；就绪后刷新服务端页面。 */
  async function refreshRankings() {
    setRefreshing(true);
    try {
      await apiSend("/api/rankings/refresh", "POST");
      for (let tries = 0; tries < 40; tries += 1) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        const res = await fetch("/api/rankings", { cache: "no-store" });
        if (res.ok) {
          router.refresh();
          return;
        }
      }
      // 轮询窗口内没等到：任务还在后台跑，提示后退出加载态
      toast("榜单还在后台抓取，稍后刷新页面即可看到");
    } catch {
      // apiSend 已处理 401 跳登录；其余失败（如任务已在跑）不中断页面
    } finally {
      setRefreshing(false);
    }
  }

  const columns: DColumn<RankingEntry>[] = [
    {
      title: "排名",
      dataIndex: "rank",
      align: "right",
      width: 72,
      sorter: (a, b) => a.rank - b.rank,
      render: (v: number) => (
        <span className="mono num" style={{ fontWeight: 550 }}>
          {v}
        </span>
      ),
    },
    {
      title: "模型",
      dataIndex: "name",
      width: 220,
      ellipsis: true,
      render: (v: string, row) => (
        <a
          href={row.url}
          target="_blank"
          rel="noreferrer"
          className="mono"
          title={v}
          style={{ color: "inherit", textDecorationColor: "var(--border-strong)" }}
        >
          {v}
        </a>
      ),
    },
    {
      title: "厂商",
      dataIndex: "creator",
      width: 130,
      ellipsis: true,
      mobileHide: true,
      render: (v: string | null) => v ?? <span style={{ color: "var(--text-3)" }}>—</span>,
    },
    {
      title: "上下文",
      dataIndex: "context_window",
      align: "right",
      width: 84,
      mobileHide: true,
      render: (v: string | null) => (
        <span className="mono num" style={{ color: "var(--text-2)" }}>
          {v ?? "—"}
        </span>
      ),
    },
    {
      title: (
        <>
          智能指数<TermTip term="aa_rank" />
        </>
      ),
      dataIndex: "intelligence_index",
      align: "right",
      width: 96,
      sorter: (a, b) => (a.intelligence_index ?? -1) - (b.intelligence_index ?? -1),
      render: (v: number | null) =>
        v == null ? (
          <span style={{ color: "var(--text-3)" }}>—</span>
        ) : (
          <span className="mono num" style={{ fontWeight: 550 }}>
            {v}
          </span>
        ),
    },
    {
      title: (
        <>
          速度
          <span className="thead-unit thead-unit-block">tokens/s</span>
        </>
      ),
      dataIndex: "median_output_tokens_per_second",
      align: "right",
      width: 100,
      mobileHide: true,
      sorter: (a, b) => (a.median_output_tokens_per_second ?? -1) - (b.median_output_tokens_per_second ?? -1),
      render: (v: number | null) => (
        <span className="mono num" style={{ color: "var(--text-2)" }}>
          {formatCount(v)}
        </span>
      ),
    },
    {
      title: (
        <>
          首字延迟
          <span className="thead-unit thead-unit-block">s</span>
        </>
      ),
      dataIndex: "latency_first_chunk_seconds",
      align: "right",
      width: 92,
      mobileHide: true,
      sorter: (a, b) => (a.latency_first_chunk_seconds ?? Infinity) - (b.latency_first_chunk_seconds ?? Infinity),
      render: (v: number | null) => (
        <span className="mono num" style={{ color: "var(--text-2)" }}>
          {formatCount(v, 1)}
        </span>
      ),
    },
  ];

  if (!data) {
    return (
      <div className="panel" style={{ padding: "32px 28px", textAlign: "center" }}>
        <div style={{ fontSize: 15, fontWeight: 550, marginBottom: 8 }}>模型榜单还没生成</div>
        <p style={{ color: "var(--text-2)", fontSize: 13.5, lineHeight: 1.8, margin: "0 auto", maxWidth: 520 }}>
          榜单收录第三方评测的智能指数排名，每天自动更新一次；着急的话，登录后点下面的按钮立刻抓一次。
        </p>
        <div style={{ marginTop: 16 }}>
          <Btn variant="primary" loading={refreshing} onClick={refreshRankings}>
            立即更新
          </Btn>
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center" }}>
        <Input
          placeholder="搜索模型或厂商"
          style={{ flex: "1 1 260px", maxWidth: "min(420px, 100%)" }}
          value={keyword}
          onChange={setKeyword}
          prefix={<IconSearch size={14} />}
        />
        <span style={{ color: "var(--text-2)", fontSize: 13 }}>
          数据时间 <span className="mono" title={data.generated_at_iso}>{formatIsoMinute(data.generated_at_iso)}</span> · 第三方评测口径，非本站评测
        </span>
      </div>
      <div className="panel rise-in" style={{ overflow: "hidden" }}>
        <DataTable<RankingEntry>
          rowKey={(row) => row.slug}
          columns={columns}
          rows={rows}
          paginated
          defaultPageSize={100}
          pageSizeStorageKey="rankings-page-size"
          scrollX={640}
          mobileScrollX={390}
          dense
          empty={
            <Empty
              icon={<DajuSit width={30} />}
              title="没有匹配的模型"
              description="换个关键词试试，或清空搜索框看完整榜单。"
            />
          }
        />
      </div>
    </div>
  );
}
