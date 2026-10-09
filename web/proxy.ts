import { NextResponse, type NextRequest } from "next/server";

const API_BASE = process.env.PRICE_WEB_API_URL ?? "http://127.0.0.1:8000";

/** 页面访问埋点：服务端把每次页面导航（首屏加载与客户端路由跳转）异步上报给
 *  FastAPI 落库，浏览器侧零脚本。排除 API 反代、Next 内部资源、静态文件与
 *  /admin 后台页面（统计只反映对外访客，管理员自己的浏览不计入）；
 *  next-router-prefetch 的预取请求不计入 PV。上报失败静默忽略，不影响页面渲染。
 *  Next 16 起该文件约定由 middleware 更名为 proxy，职责不变。 */
export function proxy(request: NextRequest) {
  if (request.headers.get("next-router-prefetch") === "1") {
    return NextResponse.next();
  }
  const ip =
    (request.headers.get("x-forwarded-for") ?? "").split(",")[0].trim() ||
    request.headers.get("x-real-ip") ||
    "";
  fetch(`${API_BASE}/api/analytics/track`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(ip ? { "x-forwarded-for": ip } : null),
      "user-agent": request.headers.get("user-agent") ?? "",
    },
    body: JSON.stringify({ path: request.nextUrl.pathname }),
  }).catch(() => {});
  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!api|admin|_next/static|_next/image|favicon\\.ico|icon|apple-icon|robots\\.txt|sitemap\\.xml|wecom-qr\\.png).*)",
  ],
};
