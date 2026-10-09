"use client";

/** 厂商定价尚未生成时的引导：首次启动后台自动同步，本组件轮询等它就绪。 */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

const POLL_INTERVAL_MS = 3000;
const POLL_MAX_TRIES = 20;

export function CatalogEmptyState() {
  const router = useRouter();

  useEffect(() => {
    // 首次启动后端会自动同步目录；这里轮询等它落库，就绪后刷新服务端页面
    let tries = 0;
    const timer = setInterval(async () => {
      tries += 1;
      try {
        const res = await fetch("/api/catalog", { cache: "no-store" });
        if (res.ok) {
          clearInterval(timer);
          router.refresh();
          return;
        }
      } catch {
        // 后端暂不可达，下一轮再试
      }
      if (tries >= POLL_MAX_TRIES) clearInterval(timer);
    }, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [router]);

  return (
    <div className="panel" style={{ padding: "32px 28px", textAlign: "center" }}>
      <div style={{ fontSize: 15, fontWeight: 550, marginBottom: 8 }}>厂商定价正在同步</div>
      <p style={{ color: "var(--text-2)", fontSize: 13.5, lineHeight: 1.8, margin: "0 auto" }}>
        后台正在从开源模型目录{" "}
        <a href="https://models.dev" target="_blank" rel="noreferrer" style={{ color: "var(--accent-text)" }}>models.dev</a>
        {" "}同步各厂商定价（几秒完成），就绪后本页自动刷新；生成后，总览才会显示厂商价折扣。
      </p>
    </div>
  );
}
