"use client";

/** 自研基础组件集：替代 antd 的按钮/弹窗/开关/复选/下拉/输入/进度/时间线/
 *  空状态/分段控制/提示条/骨架屏与全局 toast。视觉全部由 CSS 变量驱动。 */

import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { IconCheck, IconChevronDown, IconClose } from "./icons";

/* ---------- 按钮 ---------- */

export function Btn({
  children,
  variant = "ghost",
  size,
  loading,
  disabled,
  onClick,
  className,
  type,
  title,
  ariaLabel,
}: {
  children?: ReactNode;
  variant?: "primary" | "ghost" | "text";
  size?: "lg" | "sm";
  loading?: boolean;
  disabled?: boolean;
  onClick?: () => void;
  className?: string;
  type?: "button" | "submit";
  title?: string;
  ariaLabel?: string;
}) {
  return (
    <button
      type={type ?? "button"}
      title={title}
      aria-label={ariaLabel}
      disabled={disabled || loading}
      onClick={onClick}
      className={["btn", `btn-${variant}`, size ? `btn-${size}` : "", loading ? "is-loading" : "", className ?? ""]
        .filter(Boolean)
        .join(" ")}
    >
      {loading && <span className="spin" aria-hidden />}
      {children}
    </button>
  );
}

/* ---------- 弹窗 ---------- */

export function Modal({
  open,
  onClose,
  title,
  footer,
  children,
  width = 440,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
  width?: number;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="modal-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="modal" style={{ maxWidth: `min(${width}px, 100%)` }} role="dialog" aria-modal>
        <div className="modal-head">
          <div className="modal-title">{title}</div>
          <button ref={closeRef} className="modal-x" aria-label="关闭" onClick={onClose}>
            <IconClose size={15} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>
  );
}

/* ---------- 全局 toast ---------- */

type ToastItem = { id: number; text: string };
let pushToast: ((text: string) => void) | null = null;

/** 命令式轻提示：toast("保存成功")。需在 layout 挂载 <Toaster />。 */
export function toast(text: string) {
  pushToast?.(text);
}

export function Toaster() {
  const [items, setItems] = useState<ToastItem[]>([]);
  useEffect(() => {
    pushToast = (text) => {
      const id = Date.now() + Math.random();
      setItems((current) => [...current, { id, text }]);
      window.setTimeout(() => setItems((current) => current.filter((item) => item.id !== id)), 3200);
    };
    return () => {
      pushToast = null;
    };
  }, []);
  return (
    <div className="toaster" role="status">
      {items.map((item) => (
        <div key={item.id} className="toast">
          {item.text}
        </div>
      ))}
    </div>
  );
}

/* ---------- 开关 ---------- */

export function Switch({
  checked,
  defaultChecked,
  disabled,
  title,
  onChange,
}: {
  /** 受控用法：传入 checked 后由外部状态驱动 */
  checked?: boolean;
  defaultChecked?: boolean;
  disabled?: boolean;
  title?: string;
  onChange?: (checked: boolean) => void;
}) {
  const [internal, setInternal] = useState(!!defaultChecked);
  const isControlled = checked !== undefined;
  const value = isControlled ? checked : internal;
  return (
    <button
      type="button"
      role="switch"
      aria-checked={value}
      title={title}
      disabled={disabled}
      className="switch"
      onClick={() => {
        const next = !value;
        if (!isControlled) setInternal(next);
        onChange?.(next);
      }}
    >
      <span className="knob" />
    </button>
  );
}

/* ---------- 复选框 ---------- */

export function Check({
  checked,
  onChange,
  disabled,
  children,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  children: ReactNode;
}) {
  return (
    <label className={`check${disabled ? " is-disabled" : ""}`}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="box" aria-hidden>
        {checked && <IconCheck size={11} />}
      </span>
      <span>{children}</span>
    </label>
  );
}

/* ---------- 下拉（原生 select） ---------- */

export function Sel({
  value,
  defaultValue,
  onChange,
  options,
  disabled,
  style,
  title,
}: {
  value?: string;
  defaultValue?: string;
  onChange?: (value: string) => void;
  options: { value: string; label: string }[];
  disabled?: boolean;
  style?: React.CSSProperties;
  title?: string;
}) {
  return (
    <span className="sel-wrap" style={style}>
      <select
        className="sel"
        title={title}
        value={value}
        defaultValue={defaultValue}
        disabled={disabled}
        onChange={(event) => onChange?.(event.target.value)}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <IconChevronDown size={13} className="chev" />
    </span>
  );
}

/* ---------- 输入框 ---------- */

export function Input({
  value,
  defaultValue,
  placeholder,
  onChange,
  disabled,
  style,
  prefix,
}: {
  value?: string;
  defaultValue?: string;
  placeholder?: string;
  onChange?: (value: string) => void;
  disabled?: boolean;
  style?: React.CSSProperties;
  prefix?: ReactNode;
}) {
  return (
    <span className="input-wrap" style={style}>
      {prefix}
      <input
        className="input"
        value={value}
        defaultValue={defaultValue}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(event) => onChange?.(event.target.value)}
      />
    </span>
  );
}

/* ---------- 进度条 ---------- */

export function Progress({ percent }: { percent: number }) {
  return (
    <span className="prog" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
      <span className="prog-fill" style={{ width: `${Math.min(100, Math.max(0, percent))}%` }} />
    </span>
  );
}

/* ---------- 时间线 ---------- */

export function Timeline({ items }: { items: { color: string; children: ReactNode }[] }) {
  return (
    <ul className="tline">
      {items.map((item, index) => (
        <li key={index}>
          <span className="tdot" style={{ background: item.color }} />
          {item.children}
        </li>
      ))}
    </ul>
  );
}

/* ---------- 空状态 ---------- */

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

/* ---------- 分段控制 ---------- */

export function Seg({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: ReactNode }[];
}) {
  return (
    <span className="seg" role="tablist">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="tab"
          aria-selected={option.value === value}
          className={`seg-item${option.value === value ? " on" : ""}`}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </span>
  );
}

/* ---------- 提示条 ---------- */

export function Alert({
  tone = "info",
  title,
  children,
  className,
}: {
  tone?: "info" | "warn";
  title: string;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div className={`alert alert-${tone}${className ? ` ${className}` : ""}`}>
      <div>
        <div className="alert-title">{title}</div>
        {children && <div className="alert-desc">{children}</div>}
      </div>
    </div>
  );
}

/* ---------- 骨架屏 ---------- */

export function Skel({ w, h = 14, style }: { w?: number | string; h?: number; style?: React.CSSProperties }) {
  return <span className="skel" style={{ width: w ?? "100%", height: h, display: "block", ...style }} />;
}

/* ---------- 占位弹窗（功能未上线） ---------- */

export function ComingSoon({
  label,
  title,
  description,
  variant = "text",
}: {
  label: ReactNode;
  title: string;
  description: ReactNode;
  variant?: "primary" | "ghost" | "text";
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Btn variant={variant} onClick={() => setOpen(true)}>
        {label}
      </Btn>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={title}
        footer={
          <Btn variant="primary" onClick={() => setOpen(false)}>
            知道了
          </Btn>
        }
      >
        <p style={{ color: "var(--text-2)", margin: 0, fontSize: 13.5, lineHeight: 1.7 }}>{description}</p>
      </Modal>
    </>
  );
}
