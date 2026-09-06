"use client";

import { useCallback, useEffect, useState } from "react";

/** 每页条数可选项与默认值：全站 DataTable 共用。 */
export const PAGE_SIZE_OPTIONS = [20, 35, 50, 100];
export const PAGE_SIZE_DEFAULT = 35;

const STORAGE_KEY = "table-page-size";

function normalize(value: unknown): number {
  return PAGE_SIZE_OPTIONS.includes(Number(value)) ? Number(value) : PAGE_SIZE_DEFAULT;
}

/** 全站表格每页条数偏好：localStorage 记住选择，所有开启分页的表格共享。 */
export function usePageSize(): [number, (size: number) => void] {
  const [size, setSize] = useState(PAGE_SIZE_DEFAULT);

  // 首帧用默认值渲染，挂载后再读偏好，避免 SSR 与客户端水合不一致
  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) setSize(normalize(stored));
    } catch {
      // 隐私模式等拿不到 localStorage 时保持默认值
    }
  }, []);

  const update = useCallback((next: number) => {
    const valid = normalize(next);
    setSize(valid);
    try {
      localStorage.setItem(STORAGE_KEY, String(valid));
    } catch {
      // 写不进去也无所谓，本次会话内仍然生效
    }
  }, []);

  return [size, update];
}
