"use client";

/** 采集任务：最近任务列表（4 秒轮询）与触发入口。 */

import { useEffect, useState } from "react";
import { DataTable, type DColumn } from "./DataTable";
import { CollectButton } from "./CollectButton";
import { apiSend } from "@/lib/api";
import { formatTime } from "@/lib/format";
import type { TaskInfo, TasksData } from "@/lib/types";

const TASK_KIND_LABELS: Record<string, string> = {
  collect: "全站价格采集",
  "official-refresh": "官方价库刷新",
};

function StatusTag({ status }: { status: string }) {
  if (status === "done") return <span className="tag tone-green">完成</span>;
  if (status === "running") return <span className="tag tone-blue">运行中</span>;
  return <span className="tag tone-red">失败</span>;
}

export function AdminTasks() {
  const [tasks, setTasks] = useState<TaskInfo[]>([]);

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
    { key: "kind", title: "任务", render: (_v, row) => TASK_KIND_LABELS[row.kind] ?? row.kind },
    { key: "status", title: "状态", width: 90, render: (_v, row) => <StatusTag status={row.status} /> },
    { key: "started_at", title: "开始时间", width: 150, render: (v: number) => formatTime(v) },
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
        if (row.status !== "done" || !row.result) return null;
        const result = row.result as { records?: unknown[]; models_found?: number };
        return (
          <span style={{ color: "var(--text-3)", fontSize: 12.5 }}>
            {row.kind === "official-refresh"
              ? `找到 ${String(result.models_found ?? 0)} 条`
              : `${(result.records ?? []).length} 条记录`}
          </span>
        );
      },
    },
  ];

  return (
    <div className="panel" style={{ overflow: "hidden" }}>
      <DataTable<TaskInfo>
        rowKey="id"
        columns={taskColumns}
        rows={tasks}
        empty="暂无任务记录；点击「立即采集」或「刷新官方价」后会在这里跟踪。"
      />
    </div>
  );
}
