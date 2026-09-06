"use client";

import { useState } from "react";
import { App, Button, Checkbox, Modal, Space, Typography } from "antd";
import { ThunderboltOutlined } from "@ant-design/icons";
import type { TaskInfo } from "@/lib/types";
import { ToneTag } from "./ToneTag";

/** 触发一次多站点采集：默认不写历史，可勾选写入；任务进度用轮询跟踪。 */
export function CollectButton() {
  const { message } = App.useApp();
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
      message.error(`采集失败: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setSubmitting(false);
    }
  }

  const running = task?.status === "running";
  return (
    <>
      <Button
        type="primary"
        icon={<ThunderboltOutlined />}
        loading={running || submitting}
        onClick={() => {
          setTask(null);
          setOpen(true);
        }}
      >
        立即采集
      </Button>
      <Modal
        title="立即采集"
        open={open}
        onCancel={() => {
          if (!running) setOpen(false);
        }}
        footer={null}
        width="min(480px, calc(100vw - 32px))"
      >
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            将按配置逐站点请求价格接口；无法确认格式的站点会调用 AI 从证据中提取，可能产生
            AI token 费用并耗时一到数分钟。
          </Typography.Paragraph>
          <Checkbox
            checked={persist}
            onChange={(event) => setPersist(event.target.checked)}
            disabled={running}
          >
            写入历史与事件文件（不勾选则仅本次预览，不落盘）
          </Checkbox>
          {task && (
            <div>
              {task.status === "running" && <ToneTag tone="blue">采集中…</ToneTag>}
              {task.status === "done" && (
                <ToneTag tone="green">
                  完成：{String(task.result?.records ?? 0)} 条记录，
                  {String((task.result?.events as string[] | undefined)?.length ?? 0)} 个事件
                </ToneTag>
              )}
              {task.status === "failed" && <ToneTag tone="red">失败：{task.error}</ToneTag>}
              {(task.result?.errors as { site_id: string; error: string }[] | undefined)?.map(
                (item) => (
                  <Typography.Paragraph key={item.site_id} type="danger" style={{ marginBottom: 0 }}>
                    {item.site_id}: {item.error}
                  </Typography.Paragraph>
                ),
              )}
            </div>
          )}
          {!running && (
            <Button type="primary" onClick={start} loading={submitting}>
              开始采集{persist ? "（写入历史）" : "（预览）"}
            </Button>
          )}
        </Space>
      </Modal>
    </>
  );
}
