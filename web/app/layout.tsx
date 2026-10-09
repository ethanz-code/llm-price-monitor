import type { Metadata } from "next";
import { Providers } from "./providers";
import { Toaster } from "@/components/ui";
import { SiteNav } from "@/components/SiteNav";
import { SiteFooter } from "@/components/SiteFooter";
import { themeInitScript } from "@/theme";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "LLM 价格监控 — 中转站价格逐条取证",
    template: "%s · LLM 价格监控",
  },
  description:
    "LLM 中转站模型价格取证与监控：多站点价格快照、变化事件、厂商官方价与折扣率对比。所有价格事实均来自直接请求的 HTTP 响应。",
  openGraph: {
    title: "LLM 价格监控",
    description: "多站点价格快照、官方价锚定与变化事件追踪，逐条取证、可溯源。",
    type: "website",
    locale: "zh_CN",
    siteName: "LLM 价格监控",
  },
};

export default function RootLayout({ children }: React.PropsWithChildren) {
  return (
    <html lang="zh-CN" data-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body>
        <Providers>
          <SiteNav />
          <main>{children}</main>
          <SiteFooter />
          <Toaster />
        </Providers>
      </body>
    </html>
  );
}
