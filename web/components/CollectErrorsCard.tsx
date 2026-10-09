"use client";

/** 概览页「采集异常」卡片：仅管理员可见。汇总最近采集任务的警告/错误日志，
 *  支持逐条移除与一键清空（清除标记持久保存，任务详情里的完整日志不受影响）。 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { apiSend, fetchAuthState } from "@/lib/api";
import { formatTime, taskKindLabel } from "@/lib/format";
import type { TaskErrorEntry, TaskErrorsData } from "@/lib/types";
import { Btn, Empty, toast } from "./ui";
import { IconCheck, IconClose } from "./icons";

function LevelTag({ level }: { level: TaskErrorEntry["level"] }) {
  return level === "error" ? <span className="tag tone-red">错误</span> : <span className="tag tone-yellow">警告</span>;
}

export function CollectErrorsCard() {
  const [visible, setVisible] = useState(false);
  const [entries, setEntries] = useState<TaskErrorEntry[] | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(() => {
    setRefreshing(true);
    apiSend<TaskErrorsData>("/api/tasks/errors", "GET")
      .then((data) => {
        setEntries(data.entries);
        setLoadError(false);
      })
      .catch(() => setLoadError(true))
      .finally(() => setRefreshing(false));
  }, []);

  useEffect(() => {
    let alive = true;
    fetchAuthState().then((state) => {
      if (!alive) return;
      if (state?.is_admin) setVisible(true);
    });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (visible) load();
  }, [visible, load]);

  const dismiss = (key: string) => {
    setEntries((prev) => prev?.filter((entry) => entry.key !== key) ?? prev);
    apiSend(`/api/tasks/errors/${key}`, "DELETE").catch(load);
  };

  const clearAll = () => {
    setEntries([]);
    apiSend("/api/tasks/errors", "DELETE").catch(load);
    toast("已清空，之后的新问题还会出现在这里");
  };

  if (!visible) return null;
  const list = entries ?? [];

  return (
    <div className="panel" style={{ padding: "14px 18px 8px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>采集异常</h2>
        {list.length > 0 && <span className="tag tone-red">{list.length}</span>}
        <span style={{ flex: 1 }} />
        <Btn variant="ghost" size="sm" loading={refreshing} onClick={load}>
          刷新
        </Btn>
        {list.length > 0 && (
          <Btn variant="ghost" size="sm" onClick={clearAll}>
            清空
          </Btn>
        )}
        <Link href="/admin/tasks" className="btn btn-text btn-sm">
          查看全部任务
        </Link>
      </div>

      <div style={{ marginTop: 10 }}>
        {entries === null && !loadError && (
          <p style={{ color: "var(--text-3)", fontSize: 13, padding: "12px 0" }}>正在读取…</p>
        )}
        {loadError && entries === null && (
          <p style={{ color: "var(--text-3)", fontSize: 13, padding: "12px 0" }}>
            暂时读不到采集记录，点「刷新」再试一次。
          </p>
        )}
        {entries !== null && list.length === 0 && (
          <Empty
            icon={<IconCheck size={18} />}
            title="最近的采集都很顺利"
            description="采集出现警告或错误时会显示在这里。"
          />
        )}
        {list.length > 0 && (
          <div style={{ maxHeight: 340, overflowY: "auto", display: "grid", alignContent: "start" }}>
            {list.map((entry, index) => (
              <div
                key={entry.key}
                style={{
                  display: "flex",
                  gap: 10,
                  alignItems: "flex-start",
                  fontSize: 12.5,
                  lineHeight: 1.7,
                  padding: "8px 0",
                  borderBottom: index < list.length - 1 ? "1px solid var(--border)" : undefined,
                }}
              >
                <LevelTag level={entry.level} />
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ color: "var(--text-3)", marginRight: 8, whiteSpace: "nowrap" }}>
                    {taskKindLabel(entry.kind)}
                  </span>
                  <span
                    style={{
                      color: entry.level === "error" ? "var(--tone-red-text)" : "var(--text)",
                      wordBreak: "break-all",
                    }}
                  >
                    {entry.message}
                  </span>
                </span>
                <span className="mono" style={{ color: "var(--text-3)", flexShrink: 0, fontSize: 12, whiteSpace: "nowrap" }}>
                  {formatTime(entry.time)}
                </span>
                <Btn variant="text" size="sm" ariaLabel="移除这条记录" title="移除这条记录" onClick={() => dismiss(entry.key)}>
                  <IconClose size={12} />
                </Btn>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
