import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 关闭 Next 自带的 gzip：它会缓冲反代的 SSE 流式响应，AI 助手会失去逐字输出
  compress: false,
  // 浏览器只访问 3000 端口；/api 由 Next 反代到 FastAPI，保持同源
  async rewrites() {
    const api = process.env.PRICE_WEB_API_URL ?? "http://127.0.0.1:8000";
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
  // dev 模式默认只放行 localhost 的模块脚本请求；用 127.0.0.1 访问时 chunk 会 403、页面无法水合
  allowedDevOrigins: ["127.0.0.1"],
  // 浏览器 console 转发到终端只保留 error：默认连 warn 一起转发，第三方监控脚本（如阿里云 ARMS RUM）的警告会刷屏
  logging: { browserToTerminal: "error" },
};

export default nextConfig;
