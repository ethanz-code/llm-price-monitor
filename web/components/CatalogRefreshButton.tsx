"use client";

import { useState } from "react";
import { toast, Btn, Modal } from "./ui";
import { IconSync } from "./icons";
import { ToneTag } from "./ToneTag";
import type { TaskInfo } from "@/lib/types";

/** 从 models.dev 同步厂商价目录：拉取全量定价快照并按汇率换算人民币，秒级完成。 */
export function CatalogRefreshButton() {
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
      const res = await fetch("/api/catalog/refresh", {
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
        刷新厂商定价
      </Btn>
      <Modal
        open={open}
        onClose={() => { if (!running) setOpen(false); }}
        title="刷新厂商定价"
      >
        <div style={{ display: "grid", gap: 16 }}>
          <p style={{ color: "var(--text-2)", margin: 0, fontSize: 13.5, lineHeight: 1.7 }}>
            从 models.dev 重新拉取各厂商最新定价，按当前汇率换算成人民币后整体替换；
            拉不到时保持原样不动，通常几秒完成。
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
