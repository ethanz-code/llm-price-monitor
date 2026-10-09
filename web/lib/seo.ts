/** SEO 元数据出口：各公开页的 description / OpenGraph / canonical 统一从这里出。
 *  页面级 openGraph 会整组覆盖 layout 里的同名对象，站点级字段必须每次带上。 */
import type { Metadata } from "next";
import { headers } from "next/headers";
import { site } from "@/lib/copy";

/** 各公开页共用的 OpenGraph 基础字段；配图是 public/og-image.png（1200×630 品牌卡，与 icon.svg 同源绘制） */
export const SITE_OG_BASE: NonNullable<Metadata["openGraph"]> = {
  siteName: site.name,
  type: "website",
  locale: "zh_CN",
  images: [{ url: "/og-image.png", width: 1200, height: 630 }],
};

/** 公开页统一元数据：title 走 layout 的 "%s · 大橘" 模板，OG 标题补全品牌后缀；
 *  canonical 指向本页规范地址，带参数的变体（如 /catalog?view=all）一并归一。 */
export function pageMetadata(title: string, description: string, path: string): Metadata {
  return {
    title,
    description,
    alternates: { canonical: path },
    openGraph: { ...SITE_OG_BASE, title: `${title} · ${site.name}`, description },
  };
}

/** metadataBase 与 sitemap/robots 要绝对地址：从请求头反推对外协议与域名，
 *  换域名、过反代都零配置；本机访问没有 TLS 终结，回落 http。 */
export async function siteOrigin(): Promise<string> {
  const h = await headers();
  const host = h.get("x-forwarded-host") ?? h.get("host");
  if (!host) {
    throw new Error("请求缺少 Host 头，无法推导站点对外地址");
  }
  const local = host.startsWith("localhost") || host.startsWith("127.0.0.1");
  const proto = h.get("x-forwarded-proto") ?? (local ? "http" : "https");
  return `${proto}://${host}`;
}
