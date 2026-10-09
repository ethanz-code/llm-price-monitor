"use client";

import { usePathname } from "next/navigation";

/** 登录/首次设置这类认证页：全站导航、页脚、悬浮件一律不渲染，页面自成一体。 */
export function useAuthPage(): boolean {
  const pathname = usePathname();
  return pathname === "/login" || pathname === "/setup";
}
