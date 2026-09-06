/** 服务端组件专用的 API 读取层：直连 FastAPI，按需附带 Basic Auth。 */

const API_BASE = process.env.PRICE_WEB_API_URL ?? "http://127.0.0.1:8000";

function authHeaders(): Record<string, string> {
  const pass = process.env.PRICE_WEB_PASSWORD;
  if (!pass) return {};
  const user = process.env.PRICE_WEB_USERNAME ?? "admin";
  return { Authorization: `Basic ${Buffer.from(`${user}:${pass}`).toString("base64")}` };
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: authHeaders(),
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`GET ${path} 失败: HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}
