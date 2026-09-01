/** 服务端组件专用的 API 读取层：直连 FastAPI。读接口公开，无需附带凭据。 */

const API_BASE = process.env.PRICE_WEB_API_URL ?? "http://127.0.0.1:8000";

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`GET ${path} 失败: HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

/** 浏览器侧写请求：401 时浏览器原生弹出 Basic 登录框，登录后自动重试。 */
export async function apiSend<T>(path: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  if (res.status === 401) {
    throw new Error("需要管理员登录（取消登录框则无法继续）");
  }
  if (!res.ok) {
    const detail = (await res.json().catch(() => null) as { detail?: string } | null)?.detail;
    throw new Error(detail ?? `HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}
