"use client";

import { useEffect, useRef, type ReactNode } from "react";

/**
 * 悬停光晕容器：整块只挂一个指针监听（Aceternity/Magic UI spotlight 的做法），
 * 光标相对命中元素的位置写进它的 --sx/--sy 两个 CSS 变量，光晕本体是目标内的
 * .site-card-glow，纯 CSS 呈现。只写样式变量、不进 React 状态，鼠标移动不触发
 * 重渲染；触屏没有这层光，键盘用户不受影响。
 */
export function SiteGridSpotlight({
  className,
  children,
  selector = ".site-card",
}: {
  className: string;
  children: ReactNode;
  /** 命中判定用的祖先选择器：站点卡网格用默认值，CTA 这类单块场景传自身类名 */
  selector?: string;
}) {
  const frame = useRef(0);

  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  return (
    <div
      className={className}
      onPointerMove={(event) => {
        if (event.pointerType !== "mouse") return;
        const target = (event.target as HTMLElement).closest<HTMLElement>(selector);
        if (!target) return;
        cancelAnimationFrame(frame.current);
        const { clientX, clientY } = event;
        frame.current = requestAnimationFrame(() => {
          const rect = target.getBoundingClientRect();
          target.style.setProperty("--sx", `${Math.round(clientX - rect.left)}px`);
          target.style.setProperty("--sy", `${Math.round(clientY - rect.top)}px`);
        });
      }}
      onPointerLeave={() => cancelAnimationFrame(frame.current)}
    >
      {children}
    </div>
  );
}
