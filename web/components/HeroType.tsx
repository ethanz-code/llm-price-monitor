"use client";

import { useEffect, useState } from "react";
import { home } from "@/lib/copy";

const LINES = home.typeLines;
const FULL = LINES.join("");

/** Hero 标题打字机：逐字打出，ghost 撑位避免布局跳动；prefers-reduced-motion 下直接完整显示。 */
export function HeroType() {
  const [count, setCount] = useState(0);
  const done = count >= FULL.length;

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setCount(FULL.length);
      return;
    }
    const timer = setInterval(() => {
      setCount((c) => {
        if (c >= FULL.length) {
          clearInterval(timer);
          return c;
        }
        return c + 1;
      });
    }, 85);
    return () => clearInterval(timer);
  }, []);

  const first = LINES[0].length;
  const line1 = LINES[0].slice(0, Math.min(count, first));
  // 第二行前三个字是强调词「集成式」：品牌色 + 等宽字体 + 波浪划线，跟随打字进度逐字出现
  const keyLen = 3;
  const typed = count - first;
  const line2Key = LINES[1].slice(0, Math.min(Math.max(typed, 0), keyLen));
  const line2Rest = typed > keyLen ? LINES[1].slice(keyLen, typed) : "";

  return (
    <span className="type-wrap">
      {/* 撑位的隐形全文：打字过程中标题高度稳定，副标题不跳 */}
      <span aria-hidden className="type-ghost">
        {LINES[0]}
        <br />
        <span className="type-key">{LINES[1].slice(0, keyLen)}</span>
        {LINES[1].slice(keyLen)}
      </span>
      <span aria-hidden={false} className="type-live">
        {line1}
        {typed > 0 && <br />}
        {line2Key && <span className="type-key">{line2Key}</span>}
        {line2Rest}
        <span className={`type-caret${done ? " is-done" : ""}`} />
      </span>
      {/* 打字完成后亮起的挂饰：细线吊一块镀铬方块轻摆，垂在标题文字上（与页脚马赛克同质感） */}
      <span className={`type-orn${done ? " is-on" : ""}`} aria-hidden>
        <span className="type-orn-pendant">
          <span className="type-orn-thread" />
          <span className="type-orn-tile" />
        </span>
      </span>
    </span>
  );
}
