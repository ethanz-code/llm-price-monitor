"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Btn, Seg, toast } from "./ui";
import { apiSend } from "@/lib/api";
import { CatalogTable } from "./CatalogTable";
import { CatalogAllTable } from "./CatalogAllTable";
import { CatalogEmptyState } from "./CatalogEmptyState";
import type { CatalogData } from "@/lib/types";

type CatalogViewKey = "official" | "all";

/** 厂商定价页双视图：官方定价（折扣基准）与全量渠道（models.dev 所有厂商，比价参考）。 */
export function CatalogView({
  official,
  all,
  initialView = "official",
}: {
  official: CatalogData | null;
  all: CatalogData | null;
  initialView?: CatalogViewKey;
}) {
  const [view, setView] = useState<CatalogViewKey>(initialView);
  const [refreshing, setRefreshing] = useState(false);
  const router = useRouter();

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
        {view === "all" && (
          <span style={{ color: "var(--text-2)", fontSize: 13 }}>
            覆盖 models.dev 收录的全部渠道，比价参考用；折扣对比仍以官方定价为准。
          </span>
        )}
      </div>
      {view === "official" ? (
        official ? <CatalogTable data={official} /> : <CatalogEmptyState />
      ) : all ? (
        <CatalogAllTable data={all} />
      ) : (
        <div className="panel section-gap" style={{ padding: "32px 28px", textAlign: "center" }}>
          <div style={{ fontSize: 15, fontWeight: 550, marginBottom: 8 }}>全量渠道价还没生成</div>
          <p style={{ color: "var(--text-2)", fontSize: 13.5, lineHeight: 1.8, margin: "0 auto", maxWidth: 520 }}>
            它和官方定价由同一次同步一起生成；官方定价已就绪时，点下面的按钮立刻补上（需管理员登录）。
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
