"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Btn, Input, Pick, Seg, toast } from "./ui";
import { IconSearch } from "./icons";
import { apiSend } from "@/lib/api";
import { latestReleaseByVendor, vendorBlockCompare } from "@/lib/modelOrder";
import { vendorDisplay } from "@/lib/vendorNames";
import { CatalogTable } from "./CatalogTable";
import { CatalogAllTable } from "./CatalogAllTable";
import { CatalogEmptyState } from "./CatalogEmptyState";
import type { CatalogData } from "@/lib/types";
import type { RankingHit } from "@/lib/rankings";

type CatalogViewKey = "official" | "all";

/** 厂商定价页双视图：官方定价（折扣基准）与全量渠道（models.dev 所有厂商，比价参考）。 */
export function CatalogView({
  official,
  all,
  rankingsIndex = {},
  initialView = "official",
}: {
  official: CatalogData | null;
  all: CatalogData | null;
  /** AA 榜单匹配索引：模型名 → 排名/智能指数，官方定价表里给条目挂徽标用 */
  rankingsIndex?: Record<string, RankingHit>;
  initialView?: CatalogViewKey;
}) {
  const [view, setView] = useState<CatalogViewKey>(initialView);
  const [refreshing, setRefreshing] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [vendor, setVendor] = useState("all");
  const router = useRouter();

  // 官方定价视图的厂商下拉选项：与表格行序同款（各家最新发布倒序），筛选顺序跟表一致
  const vendors = useMemo(() => {
    const latest = latestReleaseByVendor(official?.models ?? {});
    return Array.from(latest.keys()).sort(vendorBlockCompare(latest));
  }, [official]);

  function switchView(next: string) {
    setView(next as CatalogViewKey);
    // 地址栏跟着切，/catalog?view=all 可直接分享或从别处跳转进来
    window.history.replaceState(null, "", next === "all" ? "/catalog?view=all" : "/catalog");
  }

  /** 手动触发目录同步（需管理员登录）；轮询全量渠道目录就绪后刷新页面。 */
  async function refreshCatalog() {
    setRefreshing(true);
    try {
      await apiSend("/api/catalog/refresh", "POST");
      for (let tries = 0; tries < 40; tries += 1) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        const res = await fetch("/api/catalog/all", { cache: "no-store" });
        if (res.ok) {
          router.refresh();
          return;
        }
      }
      toast("同步还在后台进行，稍后刷新页面即可看到");
    } catch {
      // apiSend 已处理 401 跳登录；其余失败（如任务已在跑）不中断页面
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center" }}>
        <Seg
          value={view}
          onChange={switchView}
          options={[
            { value: "official", label: "官方定价" },
            { value: "all", label: "全量渠道" },
          ]}
        />
        {view === "official" && official ? (
          <>
            <Input
              placeholder="搜索模型或厂商"
              style={{ flex: "1 1 260px", maxWidth: "min(420px, 100%)" }}
              value={keyword}
              onChange={setKeyword}
              prefix={<IconSearch size={14} />}
            />
            <Pick
              value={vendor}
              onChange={setVendor}
              style={{ width: 160, maxWidth: "100%" }}
              options={[{ value: "all", label: "全部厂商" }, ...vendors.map((v) => ({ value: v, label: vendorDisplay(v) }))]}
            />
          </>
        ) : (
          view === "all" && (
            <span style={{ color: "var(--text-2)", fontSize: 13 }}>
              覆盖各厂商收录的全部渠道条目，比价参考用；折扣仍以官方定价为准。
            </span>
          )
        )}
        <Link
          href="/rankings"
          style={{ marginLeft: "auto", color: "var(--accent-text)", fontSize: 13.5, whiteSpace: "nowrap" }}
        >
          模型榜单 →
        </Link>
        <Btn
          variant="ghost"
          loading={refreshing}
          onClick={refreshCatalog}
          title="需管理员登录：重新抓取官方目录与全部厂商定价，更新官方价与全量渠道价目录"
        >
          立即同步
        </Btn>
      </div>
      {view === "official" ? (
        official ? (
          <CatalogTable data={official} keyword={keyword} vendor={vendor} rankingsIndex={rankingsIndex} />
        ) : (
          <CatalogEmptyState />
        )
      ) : all ? (
        <CatalogAllTable data={all} />
      ) : (
        <div className="panel" style={{ padding: "32px 28px", textAlign: "center" }}>
          <div style={{ fontSize: 15, fontWeight: 550, marginBottom: 8 }}>全量渠道价还没生成</div>
          <p style={{ color: "var(--text-2)", fontSize: 13.5, lineHeight: 1.8, margin: "0 auto", maxWidth: 520 }}>
            它和官方定价是同一次同步一起出来的；官方定价已经就绪的话，点下面的按钮马上补上（需管理员登录）。
          </p>
          <div style={{ marginTop: 16 }}>
            <Btn variant="primary" loading={refreshing} onClick={refreshCatalog}>
              立即同步
            </Btn>
          </div>
        </div>
      )}
    </div>
  );
}
