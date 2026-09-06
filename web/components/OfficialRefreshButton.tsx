"use client";

import { useState } from "react";
import { toast, Btn, Modal } from "./ui";
import { IconSync } from "./icons";
import { ToneTag } from "./ToneTag";
import type { TaskInfo } from "@/lib/types";

/** 触发官方价增量刷新：有缓存的模型保留，未命中的厂商重新走 Tavily 搜索 + AI 提取。 */
export function OfficialRefreshButton() {
  const [open, setOpen] = useState(false);
  const [starting, setStarting] = useState(false);
  const [task, setTask] = useState<TaskInfo | null>(null);

  async function poll(taskId: string) {
    for (;;) {
      const res = await fetch(`/api/tasks/${taskId}`, { cache: "no-store" });
      const info = (await res.json()) as TaskInfo;
      setTask(info);
      if (info.status !== "running") return;
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
  }

  async function start() {
    setStarting(true);
    setTask(null);
    try {
      const res = await fetch("/api/official/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (res.status === 401) {
        window.location.href = "/login";
        throw new Error("需要管理员登录");
      }
      if (!res.ok) {
        const detail = ((await res.json()) as { detail?: string }).detail;
        throw new Error(detail ?? `HTTP ${res.status}`);
      }
      const { task_id: taskId } = (await res.json()) as { task_id: string };
      await poll(taskId);
    } catch (error) {
      toast(`刷新失败: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setStarting(false);
    }
  }

  const running = task?.status === "running";
  return (
    <>
      <Btn loading={running || starting} onClick={() => { setTask(null); setOpen(true); }}>
        <IconSync size={14} />
        刷新官方价
      </Btn>
      <Modal
        open={open}
        onClose={() => { if (!running) setOpen(false); }}
        title="刷新官方价"
      >
        <div style={{ display: "grid", gap: 16 }}>
          <p style={{ color: "var(--text-2)", margin: 0, fontSize: 13.5, lineHeight: 1.7 }}>
            将按内置厂商清单执行 Tavily 搜索并调用 AI 提取官方定价页，全程可能需要数分钟；
            本轮未搜到的模型会沿用上一轮结果并标记"上轮保留"。
          </p>
          {task && (
            <div style={{ display: "grid", gap: 6 }}>
              {task.status === "running" && <ToneTag tone="blue">刷新中，可稍后回到本页查看…</ToneTag>}
              {task.status === "done" && (
                <ToneTag tone="green">
                  完成：找到 {String(task.result?.models_found ?? 0)} / 共 {String(task.result?.models_total ?? 0)} 条
                </ToneTag>
              )}
              {task.status === "failed" && <ToneTag tone="red">失败：{task.error}</ToneTag>}
            </div>
          )}
          {!running && (
            <div>
              <Btn variant="primary" onClick={start} loading={starting}>
                开始刷新
              </Btn>
            </div>
          )}
        </div>
      </Modal>
    </>
  );
}
