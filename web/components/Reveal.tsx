"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

/** 滚动进入视口时淡入上移一次；prefers-reduced-motion 下直接显示。 */
export function Reveal({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setInView(true);
      return;
    }
    // 挂载时已在视口内（刷新恢复滚动位、快速下滚）就直接显示，不等 IO 回调
    const rect = el.getBoundingClientRect();
    if (rect.top < window.innerHeight && rect.bottom > 0) {
      setInView(true);
      return;
    }
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setInView(true);
          io.disconnect();
        }
      },
      { threshold: 0.1, rootMargin: "0px 0px -48px" },
    );
    io.observe(el);
    // 兜底：水合慢或 IO 迟迟不回调时强制显示——暗色下区块隐着就是整片黑，不能空太久
    const failsafe = setTimeout(() => setInView(true), 2500);
    return () => {
      io.disconnect();
      clearTimeout(failsafe);
    };
  }, []);

  return (
    <div ref={ref} className={`reveal${inView ? " is-in" : ""}`}>
      {children}
    </div>
  );
}
