"use client";

import { useCallback, useEffect, useState } from "react";

/** 每页条数可选项与默认值：全站 DataTable 共用。 */
export const PAGE_SIZE_OPTIONS = [20, 35, 50, 100];
export const PAGE_SIZE_DEFAULT = 35;

const STORAGE_KEY = "table-page-size";

function normalize(value: unknown, fallback: number = PAGE_SIZE_DEFAULT): number {
  return PAGE_SIZE_OPTIONS.includes(Number(value)) ? Number(value) : fallback;
}

/** 表格每页条数偏好：localStorage 记住选择。默认全站共用一个键；传 storageKey 的表格
 *  （如榜单页默认 100）独立记忆，不被全站偏好顶掉。fallback 是无本地偏好时的默认条数。 */
export function usePageSize(
  fallback: number = PAGE_SIZE_DEFAULT,
  storageKey: string = STORAGE_KEY,
): [number, (size: number) => void] {
  const [size, setSize] = useState(fallback);

  // 首帧用默认值渲染，挂载后再读偏好，避免 SSR 与客户端水合不一致
  useEffect(() => {
    try {
      const stored = localStorage.getItem(storageKey);
      if (stored) setSize(normalize(stored, fallback));
    } catch {
      // 隐私模式等拿不到 localStorage 时保持默认值
    }
  }, [fallback, storageKey]);

  const update = useCallback(
    (next: number) => {
      const valid = normalize(next, fallback);
      setSize(valid);
      try {
        localStorage.setItem(storageKey, String(valid));
      } catch {
        // 写不进去也无所谓，本次会话内仍然生效
      }
    },
    [fallback, storageKey],
  );

  return [size, update];
}
