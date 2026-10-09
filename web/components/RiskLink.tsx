"use client";

import { useState } from "react";
import { Btn, Check, Modal } from "./ui";

const SKIP_KEY = "risklink-confirmed";

/**
 * 外链风险提示：点击先弹确认（第三方站点免责 + 快照价≠实时价），
 * 确认后新标签打开；勾选"本次会话不再提示"后本会话内直接放行。
 * 保留 <a href> 语义，Ctrl/Cmd/Shift 修饰点击走浏览器默认行为。
 */
export function RiskLink({
  href,
  children,
  variant = "muted",
  title,
}: {
  href: string;
  children: React.ReactNode;
  /** muted：来源类弱链接；site：站点名主链接 */
  variant?: "muted" | "site";
  /** 悬停提示（截断展示的长地址用它给全文） */
  title?: string;
}) {
  const [open, setOpen] = useState(false);
  const [skip, setSkip] = useState(false);

  let host = href;
  try {
    host = new URL(href).host;
  } catch {
    // 非法 URL 交给默认行为
  }

  function openDirectly() {
    window.open(href, "_blank", "noopener,noreferrer");
  }

  function handleClick(event: React.MouseEvent) {
    if (event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    try {
      if (sessionStorage.getItem(SKIP_KEY) === "1") {
        openDirectly();
        return;
      }
    } catch {
      // 隐私模式等拿不到 sessionStorage 时每次都提示
    }
    setOpen(true);
  }

  function confirm() {
    try {
      if (skip) sessionStorage.setItem(SKIP_KEY, "1");
    } catch {
      // 同上，忽略存储失败
    }
    setOpen(false);
    openDirectly();
  }

  return (
    <>
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        onClick={handleClick}
        className={variant === "site" ? "site-link" : undefined}
        title={title}
      >
        {children}
      </a>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="即将离开本站"
        footer={
          <>
            <Btn onClick={() => setOpen(false)}>取消</Btn>
            <Btn variant="primary" onClick={confirm}>
              继续访问
            </Btn>
          </>
        }
      >
        <div style={{ display: "grid", gap: 12 }}>
          <p style={{ margin: 0, fontSize: 13.5 }}>
            你将访问第三方站点 <span className="mono">{host}</span>。
          </p>
          <p style={{ color: "var(--text-2)", margin: 0, fontSize: 13.5, lineHeight: 1.7 }}>
            本站展示的价格均为采集时刻的快照，可能与该站当前价格不同，也不构成对该站点的使用推荐，请自行评估风险。
          </p>
          <Check checked={skip} onChange={setSkip}>
            本次会话内不再提示
          </Check>
        </div>
      </Modal>
    </>
  );
}
