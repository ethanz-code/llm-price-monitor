"use client";

import { useState } from "react";
import { App, Button, Modal, Space, Typography } from "antd";
import { SyncOutlined } from "@ant-design/icons";
import type { TaskInfo } from "@/lib/types";
import { ToneTag } from "./ToneTag";

/** 触发官方价增量刷新：有缓存的模型保留，未命中的厂商重新走 Tavily 搜索 + AI 提取。 */
export function OfficialRefreshButton() {
  const { message } = App.useApp();
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
      if (!res.ok) {
        const detail = ((await res.json()) as { detail?: string }).detail;
        throw new Error(detail ?? `HTTP ${res.status}`);
      }
      const { task_id: taskId } = (await res.json()) as { task_id: string };
      await poll(taskId);
    } catch (error) {
      message.error(`刷新失败: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setStarting(false);
    }
  }

  const running = task?.status === "running";
  return (
    <>
      <Button icon={<SyncOutlined />} loading={running || starting} onClick={() => { setTask(null); setOpen(true); }}>
        刷新官方价
      </Button>
      <Modal
        title="刷新官方价"
        open={open}
        onCancel={() => {
          if (!running) setOpen(false);
        }}
        footer={null}
        width="min(480px, calc(100vw - 32px))"
      >
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            将按内置厂商清单执行 Tavily 搜索并调用 AI 提取官方定价页，全程可能需要数分钟；
            本轮未搜到的模型会沿用上一轮结果并标记“上轮保留”。
          </Typography.Paragraph>
          {task && (
            <div>
              {task.status === "running" && <ToneTag tone="blue">刷新中，可稍后回到本页查看…</ToneTag>}
              {task.status === "done" && (
                <ToneTag tone="green">
                  完成：找到 {String(task.result?.models_found ?? 0)} / 共{" "}
                  {String(task.result?.models_total ?? 0)} 条
                </ToneTag>
              )}
              {task.status === "failed" && <ToneTag tone="red">失败：{task.error}</ToneTag>}
            </div>
          )}
          {!running && (
            <Button type="primary" onClick={start} loading={starting}>
              开始刷新
            </Button>
          )}
        </Space>
      </Modal>
    </>
  );
}
