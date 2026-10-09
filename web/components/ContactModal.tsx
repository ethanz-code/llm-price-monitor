"use client";

/** 联系方式：企业微信二维码 + 对外邮箱，页脚与提建议弹窗共用。 */

import { Modal } from "./ui";

export const CONTACT_EMAIL = "service@htlabs.com.cn";

/** 企业微信二维码图片路径：替换 web/public/wecom-qr.png 即可生效。 */
export const WECOM_QR_SRC = "/wecom-qr.png";

/** 二维码展示块：size 同时约束显示宽高（源图按正方形出）。 */
export function WecomQr({ size }: { size: number }) {
  return (
    <img
      src={WECOM_QR_SRC}
      alt="企业微信二维码"
      width={size}
      height={size}
      style={{
        width: size,
        height: size,
        borderRadius: 8,
        border: "1px solid var(--border)",
      }}
    />
  );
}

/** 企业微信二维码弹窗：页脚企微图标点击后展示。 */
export function ContactModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  return (
    <Modal open={open} onClose={onClose} title="企业微信联系">
      <div
        style={{
          display: "grid",
          justifyItems: "center",
          gap: 12,
          padding: "4px 0 6px",
        }}
      >
        <WecomQr size={220} />
        <span
          style={{ fontSize: 13, color: "var(--text-3)", textAlign: "center" }}
        >
          有任何产品建议、开发需求、数据纠错都可以直接加企微沟通。
        </span>
      </div>
    </Modal>
  );
}
