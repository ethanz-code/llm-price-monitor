"use client";

/** 站点提交管理：访客申请接入的站点列表，标记处理状态；新提交会通过 WxPusher 推到微信。 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { DataTable, type DColumn } from "./DataTable";
import { Btn, Empty } from "./ui";
import { IconFeedback } from "./icons";
import { apiSend } from "@/lib/api";
import { formatTime } from "@/lib/format";

type SiteSubmission = {
  id: number;
  name: string;
  url: string;
  models: string | null;
  contact: string | null;
  status: string;
  created_at: number;
};

function StatusTag({ status }: { status: string }) {
  return status === "done" ? (
    <span className="tag tone-green">已处理</span>
  ) : (
    <span className="tag tone-blue">待处理</span>
  );
}

export function AdminSubmissions() {
  const [rows, setRows] = useState<SiteSubmission[]>([]);
  const [status, setStatus] = useState("");

  function load(filter: string) {
    const query = new URLSearchParams({ limit: "200" });
    if (filter) query.set("status", filter);
    apiSend<{ submissions: SiteSubmission[] }>(`/api/admin/site-submissions?${query}`, "GET")
      .then((data) => setRows(data.submissions))
      .catch(() => {});
  }

  useEffect(() => {
    load(status);
  }, [status]);

  async function mark(row: SiteSubmission, next: string) {
    try {
      await apiSend(`/api/admin/site-submissions/${row.id}/status`, "POST", { status: next });
      setRows((list) => list.map((item) => (item.id === row.id ? { ...item, status: next } : item)));
    } catch {
      // 保存失败时重新拉一次，保持列表与实际一致
      load(status);
    }
  }

  const columns: DColumn<SiteSubmission>[] = [
    { key: "created_at", title: "时间", width: 150, render: (_v, row) => <span className="mono" style={{ color: "var(--text-2)", fontSize: 13, whiteSpace: "nowrap" }}>{formatTime(row.created_at)}</span> },
    { key: "name", title: "站点", width: 180 },
    {
      key: "url",
      title: "地址",
      render: (_v, row) => (
        <a href={row.url} target="_blank" rel="noreferrer" className="mono" style={{ fontSize: 12.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: "block" }}>
          {row.url}
        </a>
      ),
    },
    { key: "models", title: "想监控的模型", width: 200, render: (_v, row) => row.models || "—" },
    { key: "contact", title: "联系方式", width: 140, render: (_v, row) => row.contact || "—" },
    { key: "status", title: "状态", width: 90, render: (_v, row) => <StatusTag status={row.status} /> },
    {
      key: "action",
      title: "操作",
      width: 90,
      render: (_v, row) =>
        row.status === "done" ? (
          <Btn variant="ghost" size="sm" onClick={() => mark(row, "new")}>
            重新打开
          </Btn>
        ) : (
          <Btn variant="ghost" size="sm" onClick={() => mark(row, "done")}>
            标记已处理
          </Btn>
        ),
    },
  ];

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <p className="empty" style={{ margin: 0, padding: "12px 16px", background: "var(--bg)", borderRadius: 12, border: "1px solid var(--border)" }}>
        访客在首页点「提交监控站点」后，申请会存到这里；配置了微信通知（WxPusher）的话，每条新提交都会实时推到你的微信，不用守着这个页面刷。
        还没配的话去 <Link href="/admin/settings" style={{ color: "var(--accent)" }}>系统设置 → 通知推送（WxPusher）</Link> 填上 App Token 即可。
      </p>
      <div style={{ display: "flex", gap: 8 }}>
        <select value={status} onChange={(event) => setStatus(event.target.value)} aria-label="按状态筛选" style={{ padding: "6px 10px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg)", fontSize: 13 }}>
          <option value="">全部状态</option>
          <option value="new">待处理</option>
          <option value="done">已处理</option>
        </select>
      </div>
      <div className="panel" style={{ overflow: "hidden" }}>
        <DataTable<SiteSubmission>
          rowKey="id"
          columns={columns}
          rows={rows}
          scrollX={900}
          mobileScrollX={840}
          empty={
            <Empty
              icon={<IconFeedback size={18} />}
              title="还没有站点提交"
              description="访客从首页提交站点申请后会显示在这里。"
            />
          }
        />
      </div>
    </div>
  );
}
