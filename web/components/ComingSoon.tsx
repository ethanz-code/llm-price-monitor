"use client";

import { useState } from "react";
import { Button, Modal, Typography } from "antd";
import type { ReactNode } from "react";

/** 未上线功能的体面占位：按钮 + "即将上线"说明弹窗。 */
export function ComingSoon({
  label,
  title,
  description,
  icon,
  type = "default",
}: {
  label: ReactNode;
  title: string;
  description: string;
  icon?: ReactNode;
  type?: "default" | "primary" | "text";
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button type={type} icon={icon} onClick={() => setOpen(true)}>
        {label}
      </Button>
      <Modal
        open={open}
        onCancel={() => setOpen(false)}
        footer={<Button type="primary" onClick={() => setOpen(false)}>知道了</Button>}
        title={title}
        width="min(400px, calc(100vw - 32px))"
      >
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          {description}
        </Typography.Paragraph>
      </Modal>
    </>
  );
}
