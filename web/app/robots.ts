/** 抓取规则：后台、登录与数据接口不开放收录，其余页面允许。 */
import type { MetadataRoute } from "next";
import { siteOrigin } from "@/lib/seo";

export default async function robots(): Promise<MetadataRoute.Robots> {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: ["/admin", "/api", "/login", "/setup"],
    },
    sitemap: `${await siteOrigin()}/sitemap.xml`,
  };
}
