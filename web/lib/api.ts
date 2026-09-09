/** API 访问层：服务端直连 FastAPI（读接口公开）；浏览器侧走同源代理，
 *  管理接口依赖 HttpOnly session cookie，浏览器自动携带。 */

import type { MetaData } from "./types";

// 服务端直连 FastAPI；浏览器侧必须走同源 /api 代理（直连会撞 CORS，且拿不到 HttpOnly cookie）
const API_BASE =
  typeof window === "undefined" ? (process.env.PRICE_WEB_API_URL ?? "http://127.0.0.1:8000") : "";

export async function apiGet<T>(path: string, headers?: HeadersInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store", headers });
  if (!res.ok) {
    throw new Error(`GET ${path} 失败: HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

/** 读接口允许 404：数据尚未生成时返回 null，其余错误照常抛出。 */
export async function apiGetOptional<T>(path: string): Promise<T | null> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (res.status === 404) return null;
  if (!res.ok) {
    throw new Error(`GET ${path} 失败: HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

/** 浏览器侧管理请求：401 时跳转登录页。 */
export async function apiSend<T>(path: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  if (res.status === 401) {
    if (typeof window !== "undefined") window.location.href = "/login";
    throw new Error("需要管理员登录");
  }
  if (!res.ok) {
    throw new Error(await detailOf(res));
  }
  return (await res.json()) as T;
}

/** 登录 / 登出 / 首次设置：401 不跳转，错误信息交给调用方展示。 */
export async function apiAuthPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(await detailOf(res));
  }
  return (await res.json()) as T;
}

/** 浏览器侧读取登录态；后端不可达时返回 null。 */
export async function fetchMeta(): Promise<MetaData | null> {
  try {
    const res = await fetch("/api/meta", { cache: "no-store" });
    return res.ok ? ((await res.json()) as MetaData) : null;
  } catch {
    return null;
  }
}

async function detailOf(res: Response): Promise<string> {
  const data = (await res.json().catch(() => null)) as { detail?: string } | null;
  return data?.detail ?? `HTTP ${res.status}`;
}
