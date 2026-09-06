import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Providers } from "./providers";
import { Toaster } from "@/components/ui";
import { SiteNav } from "@/components/SiteNav";
import { SiteFooter } from "@/components/SiteFooter";
import { SideFab } from "@/components/SideFab";
import { themeInitScript } from "@/theme";
import "./globals.css";

/* 字体真正落地：CSS 里声明的 Geist Sans/Mono 由此注入，缺字回退到系统栈 */
const geistSans = Geist({ subsets: ["latin"], variable: "--font-geist-sans", display: "swap" });
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono", display: "swap" });

export const metadata: Metadata = {
  title: {
    default: "LLM 价格监控 — 中转站价格逐条可溯源",
    template: "%s · LLM 价格监控",
  },
  description:
    "把各家 API 中转站的价格、折扣、渠道状态和公告放在一起，对照厂商价，买之前先查一查。",
  openGraph: {
    title: "LLM 价格监控",
    description: "各家 API 中转站的价格、折扣、渠道状态和公告，对照厂商价，买之前先查一查。",
    type: "website",
    locale: "zh_CN",
    siteName: "LLM 价格监控",
  },
};

/* 移动端视口显式声明，viewportFit 适配刘海屏安全区；themeColor 跟随系统深浅色 */
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0a0c0e" },
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
  ],
};

export default function RootLayout({ children }: React.PropsWithChildren) {
  return (
    <html
      lang="zh-CN"
      data-theme="dark"
      className={`${geistSans.variable} ${geistMono.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body>
        <Providers>
          <SiteNav />
          <main>{children}</main>
          <SiteFooter />
          <SideFab />
          <Toaster />
        </Providers>
      </body>
    </html>
  );
}
