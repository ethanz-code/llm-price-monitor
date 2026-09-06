"use client";

/** 提建议弹窗：提交到 /api/feedback 入库并触发 WxPusher 通知。 */

import { useState } from "react";
import { Btn, Input, Modal, toast } from "./ui";
import { apiSend } from "@/lib/api";

const MAX_CONTENT = 1000;

export function FeedbackModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [content, setContent] = useState("");
  const [contact, setContact] = useState("");
  const [sending, setSending] = useState(false);

  const valid = content.trim().length > 0 && content.trim().length <= MAX_CONTENT;

  async function submit() {
    if (!valid || sending) return;
    setSending(true);
    try {
      await apiSend("/api/feedback", "POST", { content: content.trim(), contact: contact.trim() || null });
      toast("建议已收到，感谢反馈");
      setContent("");
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
      title="提建议"
      footer={
        <Btn variant="primary" loading={sending} disabled={!valid} onClick={submit}>
          提交建议
        </Btn>
      }
    >
      <div style={{ display: "grid", gap: 12 }}>
        <textarea
          className="input textarea"
          rows={5}
          maxLength={MAX_CONTENT}
          placeholder="功能建议、数据纠错、想监控的站点…"
          value={content}
          onChange={(event) => setContent(event.target.value)}
        />
        <Input value={contact} onChange={setContact} placeholder="联系方式（选填，方便我们回复你）" />
      </div>
    </Modal>
  );
}
