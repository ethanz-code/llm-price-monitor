import { describe, expect, it } from "vitest";
import { articleExcerpt, getArticle, getArticles } from "./articles";

/** Markdown 文章加载器：frontmatter 解析、正文标记、摘要提取 */
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

  it("正文保留 GFM 表格与行内代码标记，交给 react-markdown 渲染", () => {
    const collect = getArticle("how-we-collect-prices");
    expect(collect?.content).toContain("| 响应长什么样 |");
    expect(collect?.content).toContain("`New-Api-User`");
    const relay = getArticle("relay-station-traps");
    expect(relay?.content).toContain("> 信号有先后");
  });

  it("摘要取正文首个段落，剥掉行内标记", () => {
    const relay = getArticle("relay-station-traps");
    expect(articleExcerpt(relay!)).toMatch(/^44\.9 元包月/);
    const collect = getArticle("how-we-collect-prices");
    expect(articleExcerpt(collect!)).toMatch(/^llmprices\.cn 的价格不是人工抄的/);
    expect(articleExcerpt(collect!, 10)).toHaveLength(12); // 截断 10 字 + 两位省略号
  });

  it("未知 slug 返回 undefined", () => {
    expect(getArticle("no-such-article")).toBeUndefined();
  });
});
