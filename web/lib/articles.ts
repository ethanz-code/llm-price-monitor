/**
 * 文章内容集中地：正文是 web/content/articles/<slug>.md 的 Markdown 文件，
 * 这里只负责加载与解析（frontmatter + 正文），页面用 react-markdown 渲染。
 * frontmatter 字段：title / subtitle / date；slug 取文件名。新增文章零配置。
 * 写作口径：像跟读者对话，事实带出处，不喊口号不排比；subtitle 就是对外的描述，
 * 列表卡片、SEO metadata、结构化数据都用它，不单独维护摘要。
 *
 * 读取走 getArticles()（react cache 单请求去重）而不是模块级常量：
 * md 文件不在模块依赖图里，模块级求值会让 dev 模式下改 md 不生效（HMR 感知不到），
 * 每请求重读则 dev 即时生效、生产 build/运行时都拿到最新内容。
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { cache } from "react";

const ARTICLES_DIR = join(process.cwd(), "content", "articles");

export interface Article {
  /** URL 路径段 /articles/<slug>，即文件名（不含 .md），英文小写连字符 */
  slug: string;
  title: string;
  subtitle: string;
  /** 发布日期 YYYY-MM-DD，展示用，也作为 sitemap 的 lastModified */
  date: string;
  /** Markdown 正文（不含 frontmatter），由详情页 react-markdown 渲染 */
  content: string;
}

/** 解析单篇：--- 包住的 frontmatter（key: value 逐行）+ Markdown 正文 */
function parseArticle(raw: string, slug: string): Article {
  const lines = raw.split("\n");
  const meta: Record<string, string> = {};
  let start = 0;
  if (lines[0]?.trim() === "---") {
    const end = lines.indexOf("---", 1);
    if (end > 0) {
      for (const line of lines.slice(1, end)) {
        const sep = line.indexOf(":");
        if (sep > 0) meta[line.slice(0, sep).trim()] = line.slice(sep + 1).trim();
      }
      start = end + 1;
    }
  }
  const title = meta.title ?? "";
  const subtitle = meta.subtitle ?? "";
  const date = meta.date ?? "";
  if (!title || !subtitle || !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    throw new Error(`文章 ${slug}.md 的 frontmatter 缺 title/subtitle/date 或 date 不是 YYYY-MM-DD`);
  }
  return { slug, title, subtitle, date, content: lines.slice(start).join("\n").trim() };
}

/** 文章清单：读 content/articles/*.md，按日期新到旧；单请求内缓存（react cache） */
export const getArticles = cache((): Article[] =>
  readdirSync(ARTICLES_DIR)
    .filter((name) => name.endsWith(".md"))
    .map((name) => parseArticle(readFileSync(join(ARTICLES_DIR, name), "utf8"), name.slice(0, -3)))
    .sort((a, b) => b.date.localeCompare(a.date)),
);

export function getArticle(slug: string): Article | undefined {
  return getArticles().find((item) => item.slug === slug);
}
