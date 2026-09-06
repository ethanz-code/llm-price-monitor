"use client";

/** 单站测试：按已保存配置立即拉取一次价格（不落盘），弹窗展示记录明细与错误；任务进度用轮询跟踪。 */

import { useState } from "react";
import { toast, Btn, Modal } from "./ui";
import { DataTable, type DColumn } from "./DataTable";
import { ToneTag } from "./ToneTag";
import { formatPrice, recordStatusKey, rowReason, statusMeta } from "@/lib/format";
import type { SiteConfig, TaskInfo } from "@/lib/types";

type TestRecord = {
  model: string | null;
  group: string | null;
  input_price: number | null;
  output_price: number | null;
  unit: string | null;
  price_status: string | null;
  requires_auth: boolean | null;
  status_reason: string | null;
};

function priceCell(value: number | null, unit: string | null) {
  return (
    <span className="mono">
      {formatPrice(value)}
      <span style={{ color: "var(--text-3)", fontSize: 12 }}> {unit?.split("/").pop() ?? ""}</span>
    </span>
  );
}

export function SiteTestButton({ site }: { site: SiteConfig }) {
  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [task, setTask] = useState<TaskInfo | null>(null);
  const [elapsed, setElapsed] = useState<number | null>(null);

  async function poll(taskId: string, startedAt: number) {
    for (;;) {
      const res = await fetch(`/api/tasks/${taskId}`, { cache: "no-store" });
      const info = (await res.json()) as TaskInfo;
      setTask(info);
      if (info.status !== "running") {
        setElapsed(Math.max(1, Math.round((Date.now() - startedAt) / 1000)));
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
  }

  async function start() {
    setSubmitting(true);
    setTask(null);
    setElapsed(null);
    const startedAt = Date.now();
    try {
      const res = await fetch("/api/collect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ site_id: site.id, persist: false }),
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
      await poll(taskId, startedAt);
    } catch (error) {
      toast(`测试失败: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setSubmitting(false);
    }
  }

  const running = task?.status === "running";
  const records = (task?.result?.records as TestRecord[] | undefined) ?? [];
  const errors = (task?.result?.errors as { site_id: string; error: string }[] | undefined) ?? [];
  // 无有效价格的记录：占位（unavailable，如 token 过期需认证）或两类单价都缺失
  const invalid = records.filter(
    (row) => row.price_status === "unavailable" || (row.input_price === null && row.output_price === null),
  );
  const allInvalid = records.length > 0 && invalid.length === records.length;
  const failReason = rowReason({ status_reason: invalid[0]?.status_reason }) ?? "所有记录均无有效价格";

  const columns: DColumn<TestRecord>[] = [
    { key: "model", title: "模型", render: (_v, row) => <span className="mono">{row.model}</span> },
    { key: "group", title: "分组", width: 90, render: (_v, row) => row.group ?? "—" },
    {
      key: "status",
      title: "状态",
      width: 80,
      render: (_v, row) => {
        const meta = statusMeta(
          recordStatusKey({ price_status: row.price_status ?? "unavailable", requires_auth: row.requires_auth === true }),
        );
        return (
          <span title={rowReason({ status_reason: row.status_reason }) ?? undefined}>
            <ToneTag tone={meta.tone}>{meta.label}</ToneTag>
          </span>
        );
      },
    },
    { key: "input", title: "输入价", width: 130, align: "right", render: (_v, row) => priceCell(row.input_price, row.unit) },
    { key: "output", title: "输出价", width: 130, align: "right", render: (_v, row) => priceCell(row.output_price, row.unit) },
  ];

  return (
    <>
      <Btn
        variant="text"
        size="sm"
        title="按已保存配置立即拉取一次价格（仅预览，不写入历史；不含未保存的修改）"
        onClick={() => {
          setTask(null);
          setElapsed(null);
          setOpen(true);
        }}
      >
        测试采集
      </Btn>
      <Modal open={open} onClose={() => { if (!running) setOpen(false); }} title={`测试站点：${site.id}`}>
        <div style={{ display: "grid", gap: 14 }}>
          <p style={{ color: "var(--text-2)", margin: 0, fontSize: 13.5, lineHeight: 1.7 }}>
            按已保存配置对该站点立即请求一次价格，仅预览不写入历史；停用中的站点也可测试。
          </p>
          {task && (
            <div style={{ display: "grid", gap: 8 }}>
              {running && <ToneTag tone="blue">测试中…</ToneTag>}
              {task.status === "done" && allInvalid && <ToneTag tone="red">失败：{failReason}</ToneTag>}
              {task.status === "done" && !allInvalid && (
                <ToneTag tone="green">
                  完成：{records.length} 条记录{elapsed !== null ? ` · 耗时 ${elapsed} 秒` : ""}
                </ToneTag>
              )}
              {task.status === "done" && !allInvalid && invalid.length > 0 && (
                <ToneTag tone="yellow">
                  警告：{invalid.length} 条无有效价格（{invalid.map((row) => row.model).join("、")}）
                </ToneTag>
              )}
              {task.status === "failed" && <ToneTag tone="red">失败：{task.error}</ToneTag>}
              {task.status === "done" && records.length > 0 && (
                <DataTable<TestRecord> rowKey={(row) => `${row.model}:${row.group ?? ""}`} columns={columns} rows={records} />
              )}
              {task.status === "done" && records.length === 0 && errors.length === 0 && (
                <p style={{ color: "var(--text-2)", margin: 0, fontSize: 13 }}>
                  没有解析到价格：请确认目标模型名与站点返回的数据一致后重试；本地解析失败时会自动交给 AI 兜底。
                </p>
              )}
              {errors.map((item) => (
                <p key={item.site_id} style={{ color: "var(--tone-red-text)", fontSize: 13, margin: 0 }}>
                  {item.site_id}: {item.error}
                </p>
              ))}
            </div>
          )}
          {!running && task === null && (
            <div>
              <Btn variant="primary" onClick={start} loading={submitting}>
                开始测试
              </Btn>
            </div>
          )}
        </div>
      </Modal>
    </>
  );
}
