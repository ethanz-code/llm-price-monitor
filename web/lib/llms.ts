/** llms.txt 出口：给大模型看的站点说明书（llmstxt.org v2 口径：H1 + blockquote + H2 链接清单）。
 *  llms.txt 保持小索引，llms-full.txt 是完整版；文案统一取自 lib/copy，避免与页面措辞漂移。 */
import { home, site } from "@/lib/copy";
import { getSiteInfo } from "@/lib/sites";
import type { SiteMeta } from "@/lib/types";

/** 公开页面清单：llms.txt 索引与 full 版共用同一份说明 */
const PAGES = [
  { path: "/", name: "首页", note: "最新价格速览、监控站点健康度、模型榜单与常见问题" },
  { path: "/overview", name: "中转站定价", note: "全部监控站点与模型的价格对照总表，每条价格附来源链接" },
  { path: "/catalog", name: "厂商定价", note: "AI 厂商官方目录价与全量渠道比价，可切换视图" },
  { path: "/rankings", name: "模型榜单", note: "第三方评测机构 Artificial Analysis 的智能指数排名" },
  { path: "/history", name: "事件追踪", note: "价格变动与站点公告的时间线事件流" },
  { path: "/calculator", name: "花费计算", note: "按单价与用量估算花费，支持缓存命中率与厂商官方价对比" },
] as const;

/** 监控站点清单行：检测档案页链接 + 站点自身地址（对大模型是关键实体信息） */
function siteLines(origin: string, sites: SiteMeta[]): string[] {
  if (sites.length === 0) {
    return ["（监控站点清单暂时拉取不到，可稍后重试或从首页「监控中的站点」小节获取。）"];
  }
  return sites.map((row) => {
    const info = getSiteInfo(row.id, row.url);
    const href = `${origin}/overview/status/${encodeURIComponent(row.id)}`;
    const note =
      `${info.name} 的渠道检测档案，监控 ${row.models.length} 个模型` +
      `${row.url ? `，站点地址 ${row.url}` : ""}${row.enabled ? "" : "（已停用）"}`;
    return `- [${info.name}](${href}): ${note}`;
  });
}

/** llms.txt：小索引版 */
export function buildLlmsTxt(origin: string, sites: SiteMeta[]): string {
  return [
    `# ${site.name}`,
    "",
    `> ${site.name} 是 API 中转站价格监测站：${site.description}持续记录各中转站的模型价格、公告和渠道可用性，不推荐、不评分，只做对照。`,
    "",
    "内容为简体中文（zh-CN）。所有价格都附来源链接，可直接点开核对；数据仅供研究参考，不构成任何使用推荐。",
    "",
    "## 页面",
    "",
    ...PAGES.map((page) => `- [${page.name}](${origin}${page.path}): ${page.note}`),
    "",
    "## 监控站点",
    "",
    ...siteLines(origin, sites),
    "",
    "## Optional",
    "",
    `- [llms-full.txt](${origin}/llms-full.txt): 本文件完整版，含数据采集方式说明与常见问题全文`,
    "",
  ].join("\n");
}

/** llms-full.txt：完整版，llms.txt 索引到的全部细节就地展开 */
export function buildLlmsFullTxt(origin: string, sites: SiteMeta[]): string {
  return [
    `# ${site.name}`,
    "",
    `> ${site.name} 是 API 中转站价格监测站：${site.description}持续记录各中转站的模型价格、公告和渠道可用性，不推荐、不评分，只做对照。`,
    "",
    "内容为简体中文（zh-CN）。价格表里的单价是站点公开标价，实际账单还受缓存命中率和各家计费口径影响，可能与按标价估算的数字有出入；数据仅供研究参考，不构成任何使用推荐。",
    "",
    "## 数据是怎么来的",
    "",
    ...home.dataPoints.map((point) => `- ${point}`),
    "- 价格和公告定时抓取，渠道状态几分钟探测一轮，历史记录全部留档可查。",
    "",
    "## 页面",
    "",
    ...PAGES.map((page) => `- [${page.name}](${origin}${page.path}): ${page.note}`),
    "",
    "## 监控站点",
    "",
    ...siteLines(origin, sites),
    "",
    "## 常见问题",
    "",
    ...home.faq.flatMap((item) => [`**${item.q}**`, "", item.a, ""]),
    "",
  ].join("\n");
}
