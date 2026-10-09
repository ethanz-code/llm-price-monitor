"use client";

/** 自研基础组件集：替代 antd 的按钮/弹窗/开关/复选/下拉/输入/进度/时间线/
 *  空状态/分段控制/提示条/骨架屏与全局 toast。视觉全部由 CSS 变量驱动。 */

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { ReactNode } from "react";
import { IconAlertCircle, IconCheck, IconChevronDown, IconClose } from "./icons";

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

/** 弹窗栈：ESC 只关最上层弹窗；body 滚动锁按引用计数，多弹窗嵌套时正确释放 */
const modalStack: number[] = [];
let modalSeq = 0;
let scrollLockPrevOverflow = "";
let scrollLockPrevPaddingRight = "";

function pushModal(): number {
  const id = ++modalSeq;
  modalStack.push(id);
  if (modalStack.length === 1) {
    scrollLockPrevOverflow = document.body.style.overflow;
    scrollLockPrevPaddingRight = document.body.style.paddingRight;
    const gap = window.innerWidth - document.documentElement.clientWidth;
    document.body.style.overflow = "hidden";
    if (gap > 0) document.body.style.paddingRight = `${gap}px`;
  }
  return id;
}

function popModal(id: number) {
  const index = modalStack.lastIndexOf(id);
  if (index >= 0) modalStack.splice(index, 1);
  if (modalStack.length === 0) {
    document.body.style.overflow = scrollLockPrevOverflow;
    document.body.style.paddingRight = scrollLockPrevPaddingRight;
  }
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select, textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

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
  const [rendered, setRendered] = useState(open);
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) {
      setRendered(true);
      return;
    }
    // 关闭时保留 DOM，播完退出动画再卸载
    if (!rendered) return;
    const timer = window.setTimeout(() => setRendered(false), 160);
    return () => window.clearTimeout(timer);
  }, [open, rendered]);

  useEffect(() => {
    if (!rendered) return;
    const id = pushModal();
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (modalStack[modalStack.length - 1] === id) onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const root = dialogRef.current;
      if (!root) return;
      const focusables = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement;
      if (!(active instanceof Node) || !root.contains(active)) {
        event.preventDefault();
        first.focus();
        return;
      }
      if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      popModal(id);
    };
  }, [rendered, onClose]);

  if (!rendered) return null;
  // 挂到 body：祖先的 transform/rise-in 会劫持 fixed 定位，导致遮罩盖不满全屏
  return createPortal(
    <div
      className={open ? "modal-overlay" : "modal-overlay modal-closing"}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        className="modal"
        style={{ maxWidth: `min(${width}px, 100%)` }}
        role="dialog"
        aria-modal
      >
        <div className="modal-head">
          <div className="modal-title">{title}</div>
          <button ref={closeRef} className="modal-x" aria-label="关闭" onClick={onClose}>
            <IconClose size={15} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>,
    document.body,
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
  suffix,
  type,
  autoComplete,
}: {
  value?: string;
  defaultValue?: string;
  placeholder?: string;
  onChange?: (value: string) => void;
  disabled?: boolean;
  style?: React.CSSProperties;
  prefix?: ReactNode;
  suffix?: ReactNode;
  type?: string;
  autoComplete?: string;
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
        type={type}
        autoComplete={autoComplete}
        onChange={(event) => onChange?.(event.target.value)}
      />
      {suffix}
    </span>
  );
}

/* ---------- 空状态 ---------- */

export function Empty({
  icon,
  title,
  description,
  action,
  children,
}: {
  icon?: ReactNode;
  title?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children?: ReactNode;
}) {
  // 兼容旧用法：<Empty>纯文本</Empty> 仍渲染为一行灰字
  if (!icon && !title && !action) {
    return <div className="empty">{children}</div>;
  }
  return (
    <div className="empty-state">
      {icon && (
        <span className="empty-icon" aria-hidden>
          {icon}
        </span>
      )}
      {title && <p className="empty-title">{title}</p>}
      <p className="empty-desc">{description ?? children}</p>
      {action && <div className="empty-action">{action}</div>}
    </div>
  );
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
  band,
}: {
  tone?: "info" | "warn";
  title: string;
  children?: ReactNode;
  className?: string;
  /** 全宽色带变体：去边框圆角，用 tone 底色通栏铺在内容区 */
  band?: boolean;
}) {
  return (
    <div className={`alert alert-${tone}${band ? " alert-band" : ""}${className ? ` ${className}` : ""}`}>
      <div>
        <div className="alert-title">{title}</div>
        {children && <div className="alert-desc">{children}</div>}
      </div>
    </div>
  );
}

/* ---------- 名词提示 ---------- */

/** 名词解释小图标：悬停/键盘聚焦弹出深色气泡。气泡 portal 到 body 并用 fixed 定位，
 *  避免被 .dtable-wrap 等滚动容器裁切。 */
export function Tip({ text }: { text: string }) {
  const [anchor, setAnchor] = useState<{ x: number; y: number; below?: boolean } | null>(null);
  const ref = useRef<HTMLSpanElement>(null);

  const show = () => {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect) return;
    // 图标贴近视口边缘时把气泡中心收进来，防止超出一屏
    const x = Math.min(Math.max(rect.left + rect.width / 2, 140), window.innerWidth - 140);
    // sticky 表头常贴近视口顶：上方空间不足时改为向下弹出
    const below = rect.top < 76;
    setAnchor({ x, y: below ? rect.bottom : rect.top, below });
  };
  const hide = () => setAnchor(null);

  // 触摸设备没有 hover：点按显示，点按外部关闭
  useEffect(() => {
    if (!anchor) return;
    const onPointerDown = (event: PointerEvent) => {
      if (ref.current && event.target instanceof Node && ref.current.contains(event.target)) return;
      setAnchor(null);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [anchor]);

  return (
    <span
      ref={ref}
      className="tip"
      tabIndex={0}
      aria-label={text}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
      onPointerUp={(event) => {
        if (event.pointerType === "touch") show();
      }}
    >
      <IconAlertCircle size={13} />
      {anchor &&
        createPortal(
          <span
            className="tip-bubble"
            role="tooltip"
            style={{ left: anchor.x, top: anchor.y, transform: anchor.below ? "translate(-50%, 10px)" : undefined }}
          >
            {text}
          </span>,
          document.body,
        )}
    </span>
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
