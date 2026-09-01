import { NextResponse, type NextRequest } from "next/server";

/**
 * 管理员门禁：设置 PRICE_WEB_PASSWORD 后，/api 的写请求（POST/PUT/PATCH/DELETE）
 * 需要管理员 Basic 凭据，与 FastAPI 侧双层校验；页面与读接口公开浏览。
 * Proxy 固定运行在 Node.js runtime，可在 next start 运行时读取环境变量。
 */

const WRITE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export default function proxy(request: NextRequest) {
  const password = process.env.PRICE_WEB_PASSWORD;
  const isWriteApi = request.nextUrl.pathname.startsWith("/api") && WRITE_METHODS.has(request.method);
  if (!password || !isWriteApi) return NextResponse.next();

  const username = process.env.PRICE_WEB_USERNAME ?? "admin";
  const header = request.headers.get("authorization") ?? "";
  if (header.startsWith("Basic ")) {
    try {
      const [user, ...rest] = Buffer.from(header.slice(6), "base64")
        .toString("utf8")
        .split(":");
      if (user === username && rest.join(":") === password) return NextResponse.next();
    } catch {
      // 解码失败按未认证处理
    }
  }
  return new NextResponse("Unauthorized", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="llm-price-monitor"' },
  });
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
