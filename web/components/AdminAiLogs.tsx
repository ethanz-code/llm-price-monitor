"use client";

/** AI 请求日志：大模型调用记录（场景/模型/耗时/token 用量/错误），支持按场景与结果筛选。 */

import { useEffect, useState } from "react";
import { DataTable, type DColumn } from "./DataTable";
import { Btn, Empty, Modal } from "./ui";
import { IconAim } from "./icons";
import { apiSend } from "@/lib/api";
import { formatTime } from "@/lib/format";

type AiLog = {
  id: number;
  ts: number;
  scene: string;
  model: string;
  status: string;
  duration_ms: number;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  error: string | null;
  prompt_excerpt: string | null;
  response_excerpt: string | null;
};

const SCENES = ["", "助手分类", "助手问答", "价格抽取", "公告提取", "token 分析"];
const STATUSES = [
  { value: "", label: "全部结果" },
  { value: "ok", label: "成功" },
  { value: "fallback", label: "换模型重试" },
  { value: "error", label: "失败" },
];

function StatusTag({ status }: { status: string }) {
  if (status === "ok") return <span className="tag tone-green">成功</span>;
  if (status === "fallback") return <span className="tag tone-blue">换模型重试</span>;
  return <span className="tag tone-red">失败</span>;
}

function tokensText(row: AiLog): string {
  if (row.total_tokens == null && row.prompt_tokens == null && row.completion_tokens == null) return "—";
  const parts = [row.prompt_tokens, row.completion_tokens].map((v) => (v == null ? "—" : String(v)));
  return `${parts.join(" + ")}${row.total_tokens != null ? ` = ${row.total_tokens}` : ""}`;
}

/** 单条日志详情：prompt 与回复原文摘要。 */
function LogDetailModal({ log, onClose }: { log: AiLog; onClose: () => void }) {
  return (
    <Modal open onClose={onClose} title={`${log.scene} · ${log.model}`} width={680}>
      <div style={{ display: "grid", gap: 10, fontSize: 13 }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10, color: "var(--text-2)" }}>
          <StatusTag status={log.status} />
          <span>{formatTime(log.ts)}</span>
          <span>耗时 {log.duration_ms}ms</span>
          {log.total_tokens != null && <span>token：{tokensText(log)}</span>}
        </div>
        {log.error && (
          <div style={{ color: "var(--tone-red-text)", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{log.error}</div>
        )}
        {log.prompt_excerpt && (
          <div>
            <div style={{ color: "var(--text-3)", marginBottom: 4 }}>发送内容（前 500 字）</div>
            <div style={{ background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 10, padding: "10px 12px", maxHeight: 220, overflowY: "auto", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
              {log.prompt_excerpt}
            </div>
          </div>
        )}
        {log.response_excerpt && (
          <div>
            <div style={{ color: "var(--text-3)", marginBottom: 4 }}>模型回复（前 500 字）</div>
            <div style={{ background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 10, padding: "10px 12px", maxHeight: 220, overflowY: "auto", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
              {log.response_excerpt}
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}

export function AdminAiLogs() {
  const [logs, setLogs] = useState<AiLog[]>([]);
  const [scene, setScene] = useState("");
  const [status, setStatus] = useState("");
  const [detail, setDetail] = useState<AiLog | null>(null);

  useEffect(() => {
    let alive = true;
    const query = new URLSearchParams({ limit: "200" });
    if (scene) query.set("scene", scene);
    if (status) query.set("status", status);
    apiSend<{ logs: AiLog[] }>(`/api/ai-logs?${query}`, "GET")
      .then((data) => {
        if (alive) setLogs(data.logs);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [scene, status]);

  const columns: DColumn<AiLog>[] = [
    { key: "ts", title: "时间", width: 150, render: (_v, row) => <span className="mono" style={{ color: "var(--text-2)", fontSize: 13, whiteSpace: "nowrap" }}>{formatTime(row.ts)}</span> },
    { key: "scene", title: "场景", width: 100 },
    { key: "model", title: "模型", width: 180, render: (_v, row) => <span style={{ fontSize: 12.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: "block" }}>{row.model}</span> },
    { key: "status", title: "结果", width: 110, render: (_v, row) => <StatusTag status={row.status} /> },
    { key: "duration_ms", title: "耗时", width: 80, align: "right", render: (v) => `${(Number(v) / 1000).toFixed(1)}s` },
    { key: "tokens", title: "token（入+出）", width: 140, align: "right", render: (_v, row) => <span className="mono" style={{ fontSize: 12.5 }}>{tokensText(row)}</span> },
    {
      key: "error",
      title: "备注",
      render: (_v, row) =>
        row.error ? (
          <span style={{ color: "var(--tone-red-text)", fontSize: 12.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: "block" }}>{row.error}</span>
        ) : null,
    },
    {
      key: "detail",
      title: "详情",
      width: 70,
      render: (_v, row) => (
        <Btn variant="ghost" size="sm" onClick={() => setDetail(row)}>
          查看
        </Btn>
      ),
    },
  ];

  return (
    <div style={{ display: "grid", gap: 12 }}>
      {detail && <LogDetailModal log={detail} onClose={() => setDetail(null)} />}
      <div style={{ display: "flex", gap: 8 }}>
        <select value={scene} onChange={(event) => setScene(event.target.value)} aria-label="按场景筛选" style={{ padding: "6px 10px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg)", fontSize: 13 }}>
          <option value="">全部场景</option>
          {SCENES.filter(Boolean).map((item) => (
            <option key={item} value={item}>{item}</option>
          ))}
        </select>
        <select value={status} onChange={(event) => setStatus(event.target.value)} aria-label="按结果筛选" style={{ padding: "6px 10px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg)", fontSize: 13 }}>
          {STATUSES.map((item) => (
            <option key={item.value} value={item.value}>{item.label}</option>
          ))}
        </select>
      </div>
      <div className="panel" style={{ overflow: "hidden" }}>
        <DataTable<AiLog>
          rowKey="id"
          columns={columns}
          rows={logs}
          scrollX={860}
          mobileScrollX={800}
          empty={
            <Empty
              icon={<IconAim size={18} />}
              title="还没有 AI 调用记录"
              description="助手问答、价格抽取等用到 AI 的操作发生后会显示在这里。"
            />
          }
        />
      </div>
    </div>
  );
}
