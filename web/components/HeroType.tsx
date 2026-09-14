"use client";

import { home } from "@/lib/copy";

const LINES = home.typeLines;
/** 第二行开头的强调词长度 */
const KEY_LEN = 3;

/** Hero 标题：静态两行，第二行开头强调词走品牌色；不做打字机/光标等展示型动效。 */
export function HeroType() {
  return (
    <>
      {LINES[0]}
      <br />
      <span className="type-key">{LINES[1].slice(0, KEY_LEN)}</span>
      {LINES[1].slice(KEY_LEN)}
    </>
  );
}
