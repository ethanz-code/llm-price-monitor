"use client";

/** 单站测试：按已保存配置立即采集一次价格+渠道状态+站点公告并入库，弹窗展示有数据的部分；任务进度用轮询跟踪。 */

import { useState } from "react";
import { toast, Btn, Modal } from "./ui";
import { DataTable, type DColumn } from "./DataTable";
import { ToneTag } from "./ToneTag";
import { formatClock, formatPrice, recordStatusKey, rowReason, statusMeta } from "@/lib/format";
import type { SiteConfig, TaskDetail } from "@/lib/types";

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

type TestNotice = { site_id: string; kind: string | null; content: string };
type TestNoticeResult = { site_id: string; outcome: string; content: string; latest?: string };
type TestStatus = { site_id: string; kind: string | null; changes: { op: string }[] };
/** 逐站价格采集状态：需认证/无数据这类"没价但不算错误"的情况只有这里带原因 */
type TestSitePriceStatus = { site_id: string; status: string | null; error: string | null };

function priceCell(value: number | null, unit: string | null) {
  return (
    <span className="mono">
      {formatPrice(value)}
      <span style={{ color: "var(--text-3)", fontSize: 12 }}> {unit?.split("/").pop() ?? ""}</span>
    </span>
  );
}

export function SiteTestButton({ site, onDone }: { site: SiteConfig; onDone?: () => void }) {
  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [elapsed, setElapsed] = useState<number | null>(null);

  // 轮询兜底：任务卡死时最多等 5 分钟，超时后不再锁死弹窗
  const POLL_MAX_MS = 5 * 60 * 1000;

  async function poll(taskId: string, startedAt: number) {
    for (;;) {
      const res = await fetch(`/api/tasks/${taskId}`, { cache: "no-store" });
      const info = (await res.json()) as TaskDetail;
      setTask(info);
      if (info.status !== "running") {
        setElapsed(Math.max(1, Math.round((Date.now() - startedAt) / 1000)));
        // 测试采集会入库并更新最近采集状态，通知外层刷新列表
        if (info.status === "done") onDone?.();
        return;
      }
      if (Date.now() - startedAt > POLL_MAX_MS) {
        // 本地按失败收尾，解锁弹窗；真实任务状态以后台任务列表为准
        setTask((prev) => (prev ? { ...prev, status: "failed", error: "测试超时：任务仍在后台执行，可稍后在任务列表查看结果" } : prev));
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
        body: JSON.stringify({ site_id: site.id, persist: true }),
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
  // 公告只在新增/更新时有记录；渠道状态只回传有变化的事件，没有就不展示
  const notices = (task?.result?.notices as TestNotice[] | undefined) ?? [];
  const noticeResults = (task?.result?.notice_results as TestNoticeResult[] | undefined) ?? [];
  const statuses = (task?.result?.statuses as TestStatus[] | undefined) ?? [];
  const sitePriceStatus = (task?.result?.site_price_status as TestSitePriceStatus[] | undefined)?.find(
    (row) => row.site_id === site.id,
  );
  const needsAuth = sitePriceStatus?.status === "auth_required";
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
        title="按已保存配置立即采集一次价格、渠道状态和站点公告，结果会写入历史（不含未保存的修改）"
        onClick={() => {
          setTask(null);
          setElapsed(null);
          setOpen(true);
        }}
      >
        测试采集
      </Btn>
      <Modal open={open} onClose={() => { if (!running) setOpen(false); }} title={`测试站点：${site.id}`} width={720}>
        <div style={{ display: "grid", gap: 14 }}>
          <p style={{ color: "var(--text-2)", margin: 0, fontSize: 13.5, lineHeight: 1.7 }}>
            按已保存配置对该站点立即采集一次：价格、渠道状态、站点公告都会拉一遍，采到什么存什么；停用中的站点也可测试。
          </p>
          {task && (
            <div style={{ display: "grid", gap: 8 }}>
              {running && <ToneTag tone="blue">测试中…</ToneTag>}
              {task.status === "done" && allInvalid && <ToneTag tone="red">失败：{failReason}</ToneTag>}
              {task.status === "done" && !allInvalid && needsAuth && (
                <>
                  <span title={sitePriceStatus?.error ?? undefined}>
                    <ToneTag tone="red">没采到价格：站点要求登录</ToneTag>
                  </span>
                  <p style={{ color: "var(--text-2)", margin: 0, fontSize: 13 }}>
                    {sitePriceStatus?.error ?? "价格接口返回 401/403"}。更新站点设置里的认证信息（token 或 cookie）后再测一次。
                  </p>
                </>
              )}
              {task.status === "done" && !allInvalid && !needsAuth && (
                <ToneTag tone={records.length > 0 ? "green" : "yellow"}>
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
                <DataTable<TestRecord>
                  rowKey={(row) => `${row.model}:${row.group ?? ""}`}
                  columns={columns}
                  rows={records}
                  scrollX={520}
                  mobileScrollX={430}
                />
              )}
              {task.status === "done" && records.length === 0 && errors.length === 0 && !needsAuth && (
                <p style={{ color: "var(--text-2)", margin: 0, fontSize: 13 }}>
                  {sitePriceStatus?.error
                    ? `没有解析到价格：${sitePriceStatus.error}。请确认目标模型名与站点返回的数据一致后重试。`
                    : "没有解析到价格：请确认目标模型名与站点返回的数据一致后重试；本地解析失败时会自动交给 AI 兜底。"}
                </p>
              )}
              {task.status === "done" && notices.length > 0 && (
                <ToneTag tone="blue">
                  公告{notices[0].kind === "notice_init" ? "新增" : "更新"}：{notices[0].content.length > 80 ? `${notices[0].content.slice(0, 80)}…` : notices[0].content}
                </ToneTag>
              )}
              {task.status === "done" && notices.length === 0 && noticeResults[0] && noticeResults[0].outcome !== "error" && (
                <ToneTag tone={noticeResults[0].outcome === "empty" ? "gray" : noticeResults[0].outcome === "unchanged" ? "green" : "gray"}>
                  {noticeResults[0].outcome === "unchanged" && "公告采集正常，内容无变化"}
                  {noticeResults[0].outcome === "empty" &&
                    (noticeResults[0].latest
                      ? `站点没有发布新公告。当前公告：${noticeResults[0].latest.length > 80 ? `${noticeResults[0].latest.slice(0, 80)}…` : noticeResults[0].latest}`
                      : "站点没有发布公告")}
                  {noticeResults[0].outcome === "none" && "未检测到公告接口（站点没有 /api/notice 且未配置公告地址）"}
                </ToneTag>
              )}
              {task.status === "done" && statuses.length > 0 && (
                <ToneTag tone="blue">
                  渠道状态{statuses[0].kind === "status_init" ? "首次采集成功" : `有变化：${statuses[0].changes.length} 处`}
                </ToneTag>
              )}
              {errors.map((item) => (
                <p key={`${item.site_id}-${item.error}`} style={{ color: "var(--tone-red-text)", fontSize: 13, margin: 0 }}>
                  {item.site_id}: {item.error}
                </p>
              ))}
              {task.logs && task.logs.length > 0 && (
                <details
                  style={{
                    border: "1px solid var(--border)",
                    borderRadius: 10,
                    padding: "8px 12px",
                    background: "var(--bg)",
                  }}
                >
                  <summary style={{ cursor: "pointer", fontSize: 13, color: "var(--text-2)" }}>
                    采集日志（{task.logs.length} 行）
                  </summary>
                  <div style={{ display: "grid", gap: 3, marginTop: 8, maxHeight: 260, overflowY: "auto" }}>
                    {task.logs.map((log, index) => (
                      <div key={index} style={{ display: "flex", gap: 10, fontSize: 12.5, lineHeight: 1.7 }}>
                        <span className="mono" style={{ color: "var(--text-3)", flexShrink: 0 }}>
                          {formatClock(log.time)}
                        </span>
                        <span
                          style={{
                            color: log.level === "error" ? "var(--tone-red-text)" : "var(--text)",
                            wordBreak: "break-all",
                          }}
                        >
                          {log.message}
                        </span>
                      </div>
                    ))}
                  </div>
                </details>
              )}
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
