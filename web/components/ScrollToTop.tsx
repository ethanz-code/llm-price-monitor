"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";

/** 路由切换后回到页面顶部：Next 只在部分导航场景自动复位，这里兜底保证每次进新页面都从顶部开始。 */
export function ScrollToTop() {
  const pathname = usePathname();

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);

  return null;
}
