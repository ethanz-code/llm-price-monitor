"use client";

/** 采集任务：最近任务列表（4 秒轮询）、触发入口与任务日志查看。 */

import { useEffect, useMemo, useRef, useState } from "react";
import { DataTable, type DColumn } from "./DataTable";
import { Btn, Empty, Modal, LoadingRows } from "./ui";
import { DajuSit } from "./DajuArt";
import { apiSend } from "@/lib/api";
import { formatClock, formatCount, formatTime, taskKindLabel } from "@/lib/format";
import type { TaskDetail, TaskInfo, TasksData } from "@/lib/types";

function StatusTag({ status }: { status: string }) {
  if (status === "done") return <span className="tag tag-quiet">完成</span>;
  if (status === "running") return <span className="tag tone-blue">运行中</span>;
  return <span className="tag tone-red">失败</span>;
}

type GroupedTask = { task: TaskInfo; dup: number };

/** 连续同型聚合：相邻的 kind+status+报错完全一致的运行并成一行标 ×N，同一条失败刷几十行只占一行。 */
function groupTasks(tasks: TaskInfo[]): GroupedTask[] {
  const groups: GroupedTask[] = [];
  for (const task of tasks) {
    const last = groups[groups.length - 1];
    if (
      last &&
      last.task.kind === task.kind &&
      last.task.status === task.status &&
      (last.task.error ?? "") === (task.error ?? "") &&
      (last.task.error_summary ?? "") === (task.error_summary ?? "")
    ) {
      last.dup += 1;
    } else {
      groups.push({ task, dup: 1 });
    }
  }
  return groups;
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
  // 首轮轮询未回来前先画骨架，避免空表闪「还没有任务」的假空态
  const [tasksReady, setTasksReady] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () => {
      apiSend<TasksData>("/api/tasks", "GET")
        .then((data) => {
          if (alive) {
            setTasks(data.tasks);
            setTasksReady(true);
          }
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

  const grouped = useMemo(() => groupTasks(tasks), [tasks]);

  const taskColumns: DColumn<GroupedTask>[] = [
    { key: "kind", title: "任务", width: 150, render: (_v, row) => taskKindLabel(row.task.kind) },
    {
      key: "status",
      title: "状态",
      width: 110,
      render: (_v, row) => (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <StatusTag status={row.task.status} />
          {row.dup > 1 && (
            <span className="mono" title={`连续 ${row.dup} 次运行结果相同`} style={{ fontSize: 12, color: "var(--text-3)" }}>
              ×{row.dup}
            </span>
          )}
        </span>
      ),
    },
    {
      key: "started_at",
      title: "开始时间",
      width: 160,
      render: (_v, row) => (
        <span className="mono" style={{ color: "var(--text-2)", fontSize: 13, whiteSpace: "nowrap" }}>
          {formatTime(row.task.started_at)}
        </span>
      ),
    },
    {
      key: "cost",
      title: "耗时",
      width: 90,
      align: "right",
      render: (_v, row) =>
        row.task.finished_at ? `${Math.max(1, Math.round(row.task.finished_at - row.task.started_at))}s` : "—",
    },
    {
      key: "note",
      title: "备注",
      render: (_v, row) => {
        if (row.task.error) {
          return (
            <span
              title={row.task.error}
              style={{ color: "var(--tone-red-text)", fontSize: 12.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: "block" }}
            >
              {row.task.error}
            </span>
          );
        }
        const task = row.task;
        const hasErrors = (task.error_count ?? 0) > 0;
        if (task.status !== "done" || (!task.result && !hasErrors)) return null;
        const result = task.result as { records?: unknown[]; models_found?: number } | undefined;
        const countText =
          task.kind === "catalog-refresh"
            ? `找到 ${formatCount(result?.models_found ?? 0)} 条`
            : `${formatCount((result?.records ?? []).length)} 条记录`;
        return (
          <span style={{ display: "grid", gap: 2 }}>
            {hasErrors && (
              <span style={{ color: "var(--tone-red-text)", fontSize: 12.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {task.error_count} 处错误：{task.error_summary}
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
        <Btn variant="ghost" size="sm" onClick={() => setDetailId(row.task.id)}>
          查看
        </Btn>
      ),
    },
  ];

  return (
    <div style={{ display: "grid", gap: 12 }}>
      {detailId && <TaskLogModal taskId={detailId} onClose={() => setDetailId(null)} />}
      <div className="panel" style={{ overflow: "hidden" }}>
        {!tasksReady ? (
          <div style={{ padding: "16px 20px" }}>
            <LoadingRows rows={6} />
          </div>
        ) : (
        <DataTable<GroupedTask>
          rowKey={(row) => row.task.id}
          columns={taskColumns}
          rows={grouped}
          scrollX={780}
          mobileScrollX={720}
          empty={
            <Empty
              icon={<DajuSit width={30} />}
                title="暂无任务记录"
                description="系统会按「系统设置」里的频率自动采集，任务进度会显示在这里。"
            />
          }
        />
        )}
      </div>
    </div>
  );
}
