import { describe, expect, it } from "vitest";
import { getArticle, getArticles } from "./articles";

/** Markdown 文章加载器：frontmatter 解析、正文标记、描述字段 */
describe("articles markdown loader", () => {
  it("加载 content/articles 下全部文章，frontmatter 解析正确", () => {
    const all = getArticles();
    expect(all.length).toBeGreaterThanOrEqual(2);
    expect(all.map((item) => item.slug)).toContain("how-we-collect-prices");
    expect(all.map((item) => item.slug)).toContain("relay-station-traps");
    const collect = getArticle("how-we-collect-prices");
    expect(collect?.title).toBe("这些价格是怎么抓下来的");
    expect(collect?.date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("每篇文章都有非空描述（subtitle），列表卡片与 SEO 描述复用它", () => {
    for (const article of getArticles()) {
      expect(article.subtitle.length).toBeGreaterThan(8);
      expect(article.subtitle).not.toMatch(/……$/); // 不再走首段截断，不应出现省略号
    }
  });

  it("正文保留 GFM 表格与行内代码标记，交给 react-markdown 渲染", () => {
    const collect = getArticle("how-we-collect-prices");
    expect(collect?.content).toContain("| 响应长什么样 |");
    expect(collect?.content).toContain("`New-Api-User`");
    const relay = getArticle("relay-station-traps");
    expect(relay?.content).toContain("> 信号有先后");
  });

  it("未知 slug 返回 undefined", () => {
    expect(getArticle("no-such-article")).toBeUndefined();
  });
});
