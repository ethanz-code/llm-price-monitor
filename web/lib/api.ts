/** API 访问层：服务端直连 FastAPI；浏览器侧走同源代理，
 *  管理接口依赖 HttpOnly session cookie，浏览器自动携带。 */

// 服务端直连 FastAPI；浏览器侧必须走同源 /api 代理（直连会撞 CORS，且拿不到 HttpOnly cookie）。
// 部署开启读接口封锁（后端 PRICE_WEB_INTERNAL_TOKEN）后，服务端请求附带令牌自证内网身份；
// 浏览器拿不到令牌，匿名只能看页面 HTML，调不到数据接口。
const API_BASE =
  typeof window === "undefined" ? (process.env.PRICE_WEB_API_URL ?? "http://127.0.0.1:8437") : "";
const INTERNAL_TOKEN = process.env.PRICE_WEB_INTERNAL_TOKEN;

function serverHeaders(): HeadersInit | undefined {
  return typeof window !== "undefined" || !INTERNAL_TOKEN
    ? undefined
    : { "x-internal-token": INTERNAL_TOKEN };
}

export async function apiGet<T>(path: string, headers?: HeadersInit): Promise<T> {
  // 额外 headers（服务端转发管理员 cookie）与内网令牌合并，令牌始终带上
  const merged = { ...(serverHeaders() as Record<string, string> | undefined), ...(headers as Record<string, string> | undefined) };
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store", headers: merged });
  if (!res.ok) {
    throw new Error(`GET ${path} 失败: HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

/** 读接口允许 404：数据尚未生成时返回 null，其余错误照常抛出。 */
export async function apiGetOptional<T>(path: string): Promise<T | null> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store", headers: serverHeaders() });
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

/** 浏览器侧读取登录态；后端不可达时返回 null。
 *  只含是否需要首次设置与是否管理员，读接口封锁后 /api/meta 对匿名关闭，
 *  导航与登录页统一改用这个公开端点。 */
export interface AuthState {
  needs_setup: boolean;
  is_admin: boolean;
}

export async function fetchAuthState(): Promise<AuthState | null> {
  try {
    const res = await fetch("/api/auth/state", { cache: "no-store" });
    return res.ok ? ((await res.json()) as AuthState) : null;
  } catch {
    return null;
  }
}

async function detailOf(res: Response): Promise<string> {
  const data = (await res.json().catch(() => null)) as { detail?: string } | null;
  return data?.detail ?? `HTTP ${res.status}`;
}
