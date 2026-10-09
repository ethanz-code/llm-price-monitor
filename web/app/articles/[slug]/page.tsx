import Link from "next/link";
import { notFound } from "next/navigation";
import type { Metadata } from "next";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { JsonLd } from "@/components/JsonLd";
import { PageHeader } from "@/components/PageHeader";
import { getArticle, getArticles } from "@/lib/articles";
import { site } from "@/lib/copy";
import { pageMetadata, siteOrigin } from "@/lib/seo";

/** 文章详情页：正文是 content/articles/<slug>.md，这里用 react-markdown 渲染（GFM 语法），
 *  标题与正文同处一栏（.article-wrap），MD 元素样式在 globals.css 的 article-body 作用域。
 *  附 Article 结构化数据（搜索与 AI 摘要），发布日期即文章的 date 字段。 */

export function generateStaticParams() {
  return getArticles().map((item) => ({ slug: item.slug }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params;
  const article = getArticle(slug);
  if (!article) return {};
  return pageMetadata(article.title, article.subtitle, `/articles/${article.slug}`);
}

/** 外链新窗口打开，站内链接（/articles/...）原窗跳转 */
function markdownLink({ href, children }: { href?: string; children?: React.ReactNode }) {
  if (href?.startsWith("http")) {
    return (
      <a href={href} target="_blank" rel="noreferrer">
        {children}
      </a>
    );
  }
  return <a href={href}>{children}</a>;
}

/** 标题文本 → 锚点 id：空格与 Markdown 行内标记归一，中文原样保留 */
function headingId(children: React.ReactNode): string {
  const text = Array.isArray(children) ? children.join("") : String(children ?? "");
  return text.replace(/[\s`*_[\]()]/g, "");
}

/** h2 带锚点 id，供页首小节导航与浏览器 # 直接定位 */
function markdownHeading({ children }: { children?: React.ReactNode }) {
  return <h2 id={headingId(children)}>{children}</h2>;
}

/** 从 Markdown 正文提取 ## 小节标题，页首生成锚点导航 */
function articleSections(content: string): string[] {
  return content
    .split("\n")
    .filter((line) => line.startsWith("## "))
    .map((line) => line.slice(3).trim());
}

export default async function ArticlePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const article = getArticle(slug);
  if (!article) notFound();
  const origin = await siteOrigin();
  const sections = articleSections(article.content);

  return (
    <div className="page">
      <JsonLd
        data={{
          "@context": "https://schema.org",
          "@type": "Article",
          headline: article.title,
          description: article.subtitle,
          datePublished: article.date,
          inLanguage: "zh-CN",
          mainEntityOfPage: `${origin}/articles/${article.slug}`,
          image: `${origin}/og-image.png`,
          publisher: { "@type": "Organization", name: site.name },
        }}
      />
      <div className="article-wrap">
        <PageHeader title={article.title} subtitle={article.subtitle} />
        <article className="article-body">
          <p className="article-meta mono num">{article.date}</p>
          {sections.length > 0 && (
            <nav className="article-toc" aria-label="本篇小节">
              {sections.map((section) => (
                <a key={section} href={`#${encodeURIComponent(headingId(section))}`}>
                  {section}
                </a>
              ))}
            </nav>
          )}
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{ a: markdownLink, h2: markdownHeading }}
          >
            {article.content}
          </ReactMarkdown>
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
