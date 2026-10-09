import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Providers } from "./providers";
import { Toaster } from "@/components/ui";
import { SiteNav } from "@/components/SiteNav";
import { SiteFooter } from "@/components/SiteFooter";
import { ScrollToTop } from "@/components/ScrollToTop";
import { AssistantDock } from "@/components/AssistantDock";
import { themeInitScript } from "@/theme";
import { site } from "@/lib/copy";
import { SITE_OG_BASE, siteOrigin } from "@/lib/seo";
import "./globals.css";

/* 字体真正落地：CSS 里声明的 Geist Sans/Mono 由此注入，缺字回退到系统栈 */
const geistSans = Geist({ subsets: ["latin"], variable: "--font-geist-sans", display: "swap" });
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono", display: "swap" });

export async function generateMetadata(): Promise<Metadata> {
  // canonical / og:image 要绝对地址基准：优先显式配置，缺省从请求头反推（见 lib/seo）
  const metadataBase = new URL(process.env.NEXT_PUBLIC_SITE_URL ?? (await siteOrigin()));
  return {
    metadataBase,
    title: {
      default: site.title,
      template: `%s · ${site.name}`,
    },
    description: site.description,
    alternates: { canonical: "/" },
    openGraph: { ...SITE_OG_BASE, title: site.name, description: site.description },
  };
}

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
      data-theme="light"
      className={`${geistSans.variable} ${geistMono.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
        {/* 给 AI 代理指路：本页说明文件在 /llms.txt（llmstxt.org 的 rel=describedby 约定） */}
        <link rel="describedby" href="/llms.txt" />
      </head>
      <body>
        <Providers>
          <SiteNav />
          <ScrollToTop />
          <main>{children}</main>
          <SiteFooter />
          <AssistantDock />
          <Toaster />
        </Providers>
      </body>
    </html>
  );
}
