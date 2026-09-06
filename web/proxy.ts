import { NextResponse, type NextRequest } from "next/server";

/**
 * 全站 Basic Auth：设置 PRICE_WEB_PASSWORD 即启用（与 FastAPI 侧共用同一组环境变量）。
 * Proxy 固定运行在 Node.js runtime，可在 next start 运行时读取环境变量。
 */

export default function proxy(request: NextRequest) {
  const password = process.env.PRICE_WEB_PASSWORD;
  if (!password) return NextResponse.next();

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
