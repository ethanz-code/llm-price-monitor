"use client";

import { useState } from "react";
import { Button, Checkbox, Modal, Space, Typography } from "antd";
import type { ReactNode } from "react";

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
}: {
  href: string;
  children: ReactNode;
  /** muted：来源类弱链接；site：站点名主链接 */
  variant?: "muted" | "site";
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
      >
        {children}
      </a>
      <Modal
        open={open}
        onCancel={() => setOpen(false)}
        title="即将离开本站"
        width="min(440px, calc(100vw - 32px))"
        footer={
          <Space>
            <Button onClick={() => setOpen(false)}>取消</Button>
            <Button type="primary" onClick={confirm}>
              继续访问
            </Button>
          </Space>
        }
      >
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Typography.Paragraph style={{ marginBottom: 0 }}>
            你将访问第三方站点 <span className="mono">{host}</span>。
          </Typography.Paragraph>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            本站展示的价格均为特定时间的取证快照，可能与该站当前价格不同，也不构成对该站点的使用推荐。请自行评估风险。
          </Typography.Paragraph>
          <Checkbox checked={skip} onChange={(event) => setSkip(event.target.checked)}>
            本次会话内不再提示
          </Checkbox>
        </Space>
      </Modal>
    </>
  );
}
