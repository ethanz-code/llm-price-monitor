import Link from "next/link";
import type { Metadata } from "next";
import { JsonLd } from "@/components/JsonLd";
import { articleExcerpt, getArticles } from "@/lib/articles";
import { pageMetadata, siteOrigin } from "@/lib/seo";

/** 文章资讯列表页：不设可见页头（与数据列表页一致），只放卡片索引；正文在 /articles/<slug>。
 *  附 ItemList 结构化数据，让搜索与 AI 摘要拿到全部篇目。 */

export const metadata: Metadata = pageMetadata(
  "文章资讯",
  "聊聊中转站这门生意的里子：低价从哪来、账单怎么算、风险在哪，一篇一篇讲清楚。",
  "/articles",
);

export default async function ArticlesPage() {
  const origin = await siteOrigin();
  const sorted = getArticles(); // 已按日期新到旧

  return (
    <div className="page">
      <h1 className="sr-only">文章资讯</h1>
      <JsonLd
        data={{
          "@context": "https://schema.org",
          "@type": "ItemList",
          name: "文章资讯",
          numberOfItems: sorted.length,
          itemListElement: sorted.map((item, index) => ({
            "@type": "ListItem",
            position: index + 1,
            name: item.title,
            url: `${origin}/articles/${item.slug}`,
            description: articleExcerpt(item, 80),
          })),
        }}
      />
      <div className="article-wrap">
        <div className="article-list">
          {sorted.map((item) => (
            <Link key={item.slug} href={`/articles/${item.slug}`} className="panel article-card">
              <div className="article-card-head">
                <h2>{item.title}</h2>
                <span className="article-meta mono num">{item.date}</span>
              </div>
              <p>{articleExcerpt(item)}</p>
              <span className="landing-more">阅读全文 →</span>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
