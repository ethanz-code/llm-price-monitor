"use client";

/** 厂商定价尚未生成时的引导：首次启动后台自动同步，本组件轮询等它就绪。 */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

const POLL_INTERVAL_MS = 3000;
const POLL_MAX_TRIES = 20;

export function CatalogEmptyState() {
  const router = useRouter();
  const [gaveUp, setGaveUp] = useState(false);

  useEffect(() => {
    // 首次启动后端会自动同步目录；这里轮询等它落库，就绪后刷新服务端页面。
    // 递归 setTimeout 而不是 setInterval：上一轮请求没返回时不叠新请求；
    // 重试到上限后停止并明说，别让「就绪后自动刷新」变成永远不来的承诺
    let alive = true;
    let timer = 0;
    let tries = 0;
    const poll = () => {
      fetch("/api/catalog", { cache: "no-store" })
        .then((res) => {
          if (!alive) return;
          if (res.ok) router.refresh();
          else schedule();
        })
        .catch(schedule);
    };
    const schedule = () => {
      if (!alive) return;
      tries += 1;
      if (tries >= POLL_MAX_TRIES) {
        setGaveUp(true);
        return;
      }
      timer = window.setTimeout(poll, POLL_INTERVAL_MS);
    };
    poll();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [router]);

  return (
    <div className="panel" style={{ padding: "32px 28px", textAlign: "center" }}>
      <div style={{ fontSize: 15, fontWeight: 550, marginBottom: 8 }}>厂商定价正在同步</div>
      <p style={{ color: "var(--text-2)", fontSize: 13.5, lineHeight: 1.8, margin: "0 auto" }}>
        {gaveUp
          ? "同步花了比预期更长的时间：稍后手动刷新本页看看；若一直这样，请管理员到后台「采集任务」里检查「厂商定价刷新」任务。"
          : "后台正在同步各厂商定价（几秒完成），就绪后本页自动刷新；生成后，总览才会显示厂商价折扣。"}
      </p>
    </div>
  );
}
