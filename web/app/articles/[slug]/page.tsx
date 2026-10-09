import Link from "next/link";
import { notFound } from "next/navigation";
import type { Metadata } from "next";
import type { ReactNode } from "react";
import { PageHeader } from "@/components/PageHeader";
import { articleExcerpt, articles, getArticle, type ArticleBlock } from "@/lib/articles";
import { pageMetadata } from "@/lib/seo";

/** 文章详情页：正文内容在 lib/articles.ts，这里只负责排版渲染。纯静态，无取数。
 *  标题与正文同处一栏（.article-wrap，720px 左对齐），不做宽页头 + 窄正文的错位布局。 */

export function generateStaticParams() {
  return articles.map((item) => ({ slug: item.slug }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params;
  const article = getArticle(slug);
  if (!article) return {};
  return pageMetadata(article.title, articleExcerpt(article, 80), `/articles/${article.slug}`);
}

/** 行内标记：**加粗** 转 <strong>，[文字](链接) 转新窗口外链，一次扫描处理两种 */
function renderInline(text: string): ReactNode[] {
  const tokens = text.split(/(\*\*.+?\*\*|\[[^\]]+\]\([^)]+\))/g);
  return tokens.map((token, i) => {
    const bold = /^\*\*(.+)\*\*$/.exec(token);
    if (bold) return <strong key={i}>{bold[1]}</strong>;
    const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token);
    if (link) {
      return (
        <a key={i} href={link[2]} target="_blank" rel="noreferrer">
          {link[1]}
        </a>
      );
    }
    return token;
  });
}

function Block({ block }: { block: ArticleBlock }) {
  switch (block.type) {
    case "h2":
      return <h2>{block.text}</h2>;
    case "p":
      return <p>{renderInline(block.text)}</p>;
    case "quote":
      return <blockquote>{renderInline(block.text)}</blockquote>;
    case "ul":
      return (
        <ul>
          {block.items.map((item, i) => (
            <li key={i}>{renderInline(item)}</li>
          ))}
        </ul>
      );
  }
}

export default async function ArticlePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const article = getArticle(slug);
  if (!article) notFound();

  return (
    <div className="page">
      <div className="article-wrap">
        <PageHeader title={article.title} subtitle={article.subtitle} />
        <article className="article-body">
          <p className="article-meta mono num">{article.date}</p>
          {article.blocks.map((block, i) => (
            <Block key={i} block={block} />
          ))}
        </article>
        <div className="article-back">
          <Link href="/articles" className="landing-more">
            ← 返回文章资讯
          </Link>
        </div>
      </div>
    </div>
  );
}
