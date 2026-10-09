import Link from "next/link";
import type { Metadata } from "next";
import { articleExcerpt, articles } from "@/lib/articles";
import { pageMetadata } from "@/lib/seo";

/** 文章资讯列表页：不设可见页头（与数据列表页一致），只放卡片索引；正文在 /articles/<slug>。 */

export const metadata: Metadata = pageMetadata(
  "文章资讯",
  "聊聊中转站这门生意的里子：低价从哪来、账单怎么算、风险在哪，一篇一篇讲清楚。",
  "/articles",
);

export default function ArticlesPage() {
  const sorted = [...articles].sort((a, b) => b.date.localeCompare(a.date));

  return (
    <div className="page">
      <div className="article-wrap">
        <h1 className="sr-only">文章资讯</h1>
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
