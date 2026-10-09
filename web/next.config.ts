import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 浏览器只访问 3000 端口；/api 由 Next 反代到 FastAPI，保持同源
  async rewrites() {
    const api = process.env.PRICE_WEB_API_URL ?? "http://127.0.0.1:8000";
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
};

export default nextConfig;
