"use client";

import { useState } from "react";

interface FaqItem {
  q: string;
  a: string;
}

/** 首页常见问题：单开手风琴，高度用 grid-template-rows 过渡，展开收起都顺滑 */
export function FaqList({ items }: { items: readonly FaqItem[] }) {
  const [openIndex, setOpenIndex] = useState<number | null>(null);

  return (
    <div className="faq-list">
      {items.map((item, i) => {
        const isOpen = openIndex === i;
        return (
          <div className={isOpen ? "faq-item open" : "faq-item"} key={item.q}>
            <button
              type="button"
              className="faq-question"
              aria-expanded={isOpen}
              onClick={() => setOpenIndex(isOpen ? null : i)}
            >
              {item.q}
            </button>
            <div className="faq-body">
              <div className="faq-body-inner">
                <p>{item.a}</p>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
