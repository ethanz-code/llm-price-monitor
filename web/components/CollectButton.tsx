"use client";

import { useState } from "react";
import { toast, Btn, Modal, Check } from "./ui";
import { IconBolt } from "./icons";
import { ToneTag } from "./ToneTag";
import type { TaskInfo } from "@/lib/types";

/** 触发一次多站点采集：默认不写历史，可勾选写入；任务进度用轮询跟踪。 */
export function CollectButton({ size }: { size?: "lg" | "sm" }) {
  const [open, setOpen] = useState(false);
  const [persist, setPersist] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [task, setTask] = useState<TaskInfo | null>(null);

  async function poll(taskId: string) {
    for (;;) {
      const res = await fetch(`/api/tasks/${taskId}`, { cache: "no-store" });
      const info = (await res.json()) as TaskInfo;
      setTask(info);
      if (info.status !== "running") return;
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
  }

  async function start() {
    setSubmitting(true);
    setTask(null);
    try {
      const res = await fetch("/api/collect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ persist }),
      });
      if (!res.ok) {
        const detail = ((await res.json()) as { detail?: string }).detail;
        throw new Error(detail ?? `HTTP ${res.status}`);
      }
      const { task_id: taskId } = (await res.json()) as { task_id: string };
      await poll(taskId);
    } catch (error) {
      toast(`采集失败: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setSubmitting(false);
    }
  }

  const running = task?.status === "running";
  const records = (task?.result?.records as unknown[] | undefined)?.length ?? 0;
  const events = (task?.result?.events as string[] | undefined)?.length ?? 0;
  return (
    <>
      <Btn variant="primary" size={size} loading={running || submitting} onClick={() => { setTask(null); setOpen(true); }}>
        <IconBolt size={14} />
        立即采集
      </Btn>
      <Modal
        open={open}
        onClose={() => { if (!running) setOpen(false); }}
        title="立即采集"
      >
        <div style={{ display: "grid", gap: 16 }}>
          <p style={{ color: "var(--text-2)", margin: 0, fontSize: 13.5, lineHeight: 1.7 }}>
            将按配置逐站点请求价格接口；无法确认格式的站点会调用 AI 从证据中提取，可能产生
            AI token 费用并耗时一到数分钟。
          </p>
          <Check checked={persist} onChange={setPersist} disabled={running}>
            写入历史与事件文件（不勾选则仅本次预览，不落盘）
          </Check>
          {task && (
            <div style={{ display: "grid", gap: 6 }}>
              {task.status === "running" && <ToneTag tone="blue">采集中…</ToneTag>}
              {task.status === "done" && (
                <ToneTag tone="green">
                  完成：{String(records)} 条记录，{String(events)} 个事件
                </ToneTag>
              )}
              {task.status === "failed" && <ToneTag tone="red">失败：{task.error}</ToneTag>}
              {(task.result?.errors as { site_id: string; error: string }[] | undefined)?.map((item) => (
                <p key={item.site_id} style={{ color: "var(--tone-red-text)", fontSize: 13, margin: 0 }}>
                  {item.site_id}: {item.error}
                </p>
              ))}
            </div>
          )}
          {!running && (
            <div>
              <Btn variant="primary" onClick={start} loading={submitting}>
                开始采集{persist ? "（写入历史）" : "（预览）"}
              </Btn>
            </div>
          )}
        </div>
      </Modal>
    </>
  );
}
