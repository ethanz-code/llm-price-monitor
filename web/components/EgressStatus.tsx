"use client";

/** 管理台侧栏的采集出口状态条：实时探测备用代理连通性；未配置代理时不渲染。
 *  每次完整加载管理台自动测一次，点状态条随时重测；不轮询——采集链路自己有
 *  失败记忆兜底，这里的红绿只是给管理员看的仪表。 */
import { useCallback, useEffect, useState } from "react";
import { apiSend } from "@/lib/api";

type ProxyStatus = {
  configured: boolean;
  ok?: boolean;
  elapsed_ms?: number;
  exit_ip?: string | null;
  error?: string;
};

export function EgressStatus() {
  const [status, setStatus] = useState<ProxyStatus | null>(null);
  const [checking, setChecking] = useState(false);

  const check = useCallback(async () => {
    setChecking(true);
    try {
      setStatus(await apiSend<ProxyStatus>("/api/settings/proxy-status", "GET"));
    } catch (error) {
      setStatus({ configured: true, ok: false, error: error instanceof Error ? error.message : "状态获取失败" });
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    void check();
  }, [check]);

  if (!status || !status.configured) return null;

  const tone = checking
    ? "var(--text-3)"
    : status.ok
      ? "var(--tone-green-text)"
      : "var(--tone-red-text)";
  const label = checking ? "出口检测中…" : status.ok ? `出口连通 ${status.elapsed_ms}ms` : "出口不通";
  const tip = checking
    ? "正在检测备用代理连通性"
    : status.ok
      ? `备用代理可用，出口 IP ${status.exit_ip ?? "未知"}；点击重测`
      : `备用代理不可用：${status.error ?? "未知原因"}；点击重测`;

  return (
    <button
      type="button"
      onClick={check}
      disabled={checking}
      title={tip}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        fontSize: 12,
        color: tone,
        background: "none",
        border: "none",
        padding: "6px 10px",
        cursor: checking ? "default" : "pointer",
        textAlign: "left",
      }}
    >
      <span
        style={{
          width: 7,
          height: 7,
          borderRadius: "50%",
          flexShrink: 0,
          background: checking
            ? "var(--text-3)"
            : status.ok
              ? "var(--tone-green-text)"
              : "var(--tone-red-text)",
        }}
      />
      {label}
    </button>
  );
}
