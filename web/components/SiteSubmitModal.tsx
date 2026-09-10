"use client";

/** 提交监控站点弹窗：提交到 /api/site-submissions 入库并触发 WxPusher 通知。 */

import { useState } from "react";
import { Btn, Input, Modal, toast } from "./ui";
import { apiSend } from "@/lib/api";

export function SiteSubmitModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [models, setModels] = useState("");
  const [contact, setContact] = useState("");
  const [sending, setSending] = useState(false);

  const valid = name.trim().length > 0 && /^https?:\/\//.test(url.trim());

  async function submit() {
    if (!valid || sending) return;
    setSending(true);
    try {
      await apiSend("/api/site-submissions", "POST", {
        name: name.trim(),
        url: url.trim(),
        models: models.trim() || null,
        contact: contact.trim() || null,
      });
      toast("已收到你的站点，我们会尽快核验接入");
      setName("");
      setUrl("");
      setModels("");
      setContact("");
      onClose();
    } catch (error) {
      toast(`提交失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setSending(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="提交监控站点"
      footer={
        <Btn variant="primary" loading={sending} disabled={!valid} onClick={submit}>
          提交站点
        </Btn>
      }
    >
      <div style={{ display: "grid", gap: 12 }}>
        <Input value={name} onChange={setName} placeholder="站点名称，例如：Example 中转" ariaLabel="站点名称" />
        <Input value={url} onChange={setUrl} placeholder="站点地址，以 https:// 开头" ariaLabel="站点地址" />
        <Input
          value={models}
          onChange={setModels}
          placeholder="想监控的模型（选填，逗号分隔）"
          ariaLabel="想监控的模型"
        />
        <Input value={contact} onChange={setContact} placeholder="联系方式（选填，方便反馈接入结果）" ariaLabel="联系方式" />
      </div>
    </Modal>
  );
}

/** 首页用的入口按钮：页面本身是服务端组件，弹窗开关收在这个客户端组件里。 */
export function SiteSubmitButton({ label, variant = "text" }: { label: string; variant?: "primary" | "ghost" | "text" }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Btn variant={variant} onClick={() => setOpen(true)}>
        {label}
      </Btn>
      <SiteSubmitModal open={open} onClose={() => setOpen(false)} />
    </>
  );
}
