/** 全站 sitemap：静态页 + 每个监控站点的检测档案页。
 *  站点清单来自 /api/meta，拉取失败只少列检测档案页，不阻塞整份 sitemap。 */
import type { MetadataRoute } from "next";
import { apiGetOptional, PUBLIC_REVALIDATE } from "@/lib/api";
import { articles } from "@/lib/articles";
import { siteOrigin } from "@/lib/seo";
import type { MetaData } from "@/lib/types";

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const origin = await siteOrigin();
  const now = new Date();
  const staticRoutes: MetadataRoute.Sitemap = [
    { url: `${origin}/`, lastModified: now, changeFrequency: "hourly", priority: 1 },
    { url: `${origin}/overview`, lastModified: now, changeFrequency: "hourly", priority: 0.9 },
    { url: `${origin}/catalog`, lastModified: now, changeFrequency: "daily", priority: 0.8 },
    { url: `${origin}/rankings`, lastModified: now, changeFrequency: "daily", priority: 0.7 },
    { url: `${origin}/history`, lastModified: now, changeFrequency: "hourly", priority: 0.7 },
    { url: `${origin}/discover`, lastModified: now, changeFrequency: "daily", priority: 0.6 },
    { url: `${origin}/calculator`, lastModified: now, changeFrequency: "weekly", priority: 0.5 },
  ];
  const articleRoutes: MetadataRoute.Sitemap = articles.map((item) => ({
    url: `${origin}/articles/${item.slug}`,
    lastModified: item.date,
    changeFrequency: "monthly",
    priority: 0.4,
  }));
  const meta = await apiGetOptional<MetaData>("/api/meta", PUBLIC_REVALIDATE).catch(() => null);
  const statusRoutes: MetadataRoute.Sitemap = (meta?.sites ?? []).map((row) => ({
    url: `${origin}/overview/status/${encodeURIComponent(row.id)}`,
    lastModified: now,
    changeFrequency: "daily",
    priority: 0.6,
  }));
  return [...staticRoutes, ...articleRoutes, ...statusRoutes];
}
