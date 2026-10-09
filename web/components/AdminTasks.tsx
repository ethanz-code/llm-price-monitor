"use client";

/** 采集任务：最近任务列表（4 秒轮询）、触发入口与任务日志查看。 */

import { useEffect, useRef, useState } from "react";
import { DataTable, type DColumn } from "./DataTable";
import { Btn, Empty, Modal } from "./ui";
import { IconBolt } from "./icons";
import { apiSend } from "@/lib/api";
import { formatClock, formatTime, taskKindLabel } from "@/lib/format";
import type { TaskDetail, TaskInfo, TasksData } from "@/lib/types";

function StatusTag({ status }: { status: string }) {
  if (status === "done") return <span className="tag tone-green">完成</span>;
  if (status === "running") return <span className="tag tone-blue">运行中</span>;
  return <span className="tag tone-red">失败</span>;
}

/** 任务日志弹窗：运行中的任务每 2 秒刷新并滚动到最新一行。 */
function TaskLogModal({ taskId, onClose }: { taskId: string; onClose: () => void }) {
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    let timer = 0;
    const load = () => {
      apiSend<TaskDetail>(`/api/tasks/${taskId}`, "GET")
        .then((data) => {
          if (!alive) return;
          setDetail(data);
          if (data.status === "running") timer = window.setTimeout(load, 2000);
        })
        .catch(() => {});
    };
    load();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [taskId]);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [detail]);

  return (
    <Modal open onClose={onClose} title="任务日志" width={680}>
      {!detail ? (
        <div style={{ padding: "32px 0", textAlign: "center", color: "var(--text-3)", fontSize: 13 }}>
          正在读取日志…
        </div>
      ) : (
        <div style={{ display: "grid", gap: 10 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 13, color: "var(--text-2)" }}>
            <StatusTag status={detail.status} />
            <span>{taskKindLabel(detail.kind)}</span>
            <span style={{ color: "var(--text-3)" }}>
              开始 {formatTime(detail.started_at)}
              {detail.finished_at ? ` · 耗时 ${Math.max(1, Math.round(detail.finished_at - detail.started_at))}s` : ""}
            </span>
          </div>
          <div
            ref={listRef}
            style={{
              maxHeight: 420,
              overflowY: "auto",
            background: "var(--bg)",
            border: "1px solid var(--border)",
              borderRadius: 10,
              padding: "10px 12px",
              display: "grid",
              gap: 3,
              alignContent: "start",
            }}
          >
            {detail.logs.length === 0 && (
              <span style={{ color: "var(--text-3)", fontSize: 12.5 }}>这次任务还没有日志。</span>
            )}
            {detail.logs.map((log, index) => (
              <div key={index} style={{ display: "flex", gap: 10, fontSize: 12.5, lineHeight: 1.7 }}>
                <span className="mono" style={{ color: "var(--text-3)", flexShrink: 0 }}>
                  {formatClock(log.time)}
                </span>
                <span style={{ color: log.level === "error" ? "var(--tone-red-text)" : "var(--text)", wordBreak: "break-all" }}>
                  {log.message}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </Modal>
  );
}

export function AdminTasks() {
  const [tasks, setTasks] = useState<TaskInfo[]>([]);
  const [detailId, setDetailId] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => {
      apiSend<TasksData>("/api/tasks", "GET")
        .then((data) => {
          if (alive) setTasks(data.tasks);
        })
        .catch(() => {});
    };
    load();
    const timer = window.setInterval(load, 4000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  const taskColumns: DColumn<TaskInfo>[] = [
    { key: "kind", title: "任务", width: 150, render: (_v, row) => taskKindLabel(row.kind) },
    { key: "status", title: "状态", width: 90, render: (_v, row) => <StatusTag status={row.status} /> },
    { key: "started_at", dataIndex: "started_at", title: "开始时间", width: 160, render: (v: number) => <span className="mono" style={{ color: "var(--text-2)", fontSize: 13, whiteSpace: "nowrap" }}>{formatTime(v)}</span> },
    {
      key: "cost",
      title: "耗时",
      width: 90,
      align: "right",
      render: (_v, row) =>
        row.finished_at ? `${Math.max(1, Math.round(row.finished_at - row.started_at))}s` : "—",
    },
    {
      key: "note",
      title: "备注",
      render: (_v, row) => {
        if (row.error) return <span style={{ color: "var(--tone-red-text)", fontSize: 12.5 }}>{row.error}</span>;
        const hasErrors = (row.error_count ?? 0) > 0;
        if (row.status !== "done" || (!row.result && !hasErrors)) return null;
        const result = row.result as { records?: unknown[]; models_found?: number } | undefined;
        const countText =
          row.kind === "catalog-refresh"
            ? `找到 ${String(result?.models_found ?? 0)} 条`
            : `${(result?.records ?? []).length} 条记录`;
        return (
          <span style={{ display: "grid", gap: 2 }}>
            {hasErrors && (
              <span style={{ color: "var(--tone-red-text)", fontSize: 12.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {row.error_count} 处错误：{row.error_summary}
              </span>
            )}
            {result && <span style={{ color: "var(--text-3)", fontSize: 12.5 }}>{countText}</span>}
          </span>
        );
      },
    },
    {
      key: "logs",
      title: "日志",
      width: 80,
      render: (_v, row) => (
        <Btn variant="ghost" size="sm" onClick={() => setDetailId(row.id)}>
          查看
        </Btn>
      ),
    },
  ];

  return (
    <div style={{ display: "grid", gap: 12 }}>
      {detailId && <TaskLogModal taskId={detailId} onClose={() => setDetailId(null)} />}
      <div className="panel" style={{ overflow: "hidden" }}>
        <DataTable<TaskInfo>
          rowKey="id"
          columns={taskColumns}
          rows={tasks}
          scrollX={780}
          mobileScrollX={720}
          empty={
            <Empty
              icon={<IconBolt size={18} />}
                title="暂无任务记录"
                description="系统会按「系统设置」里的频率自动采集，任务进度会显示在这里。"
            />
          }
        />
      </div>
    </div>
  );
}
