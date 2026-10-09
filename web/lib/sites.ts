/** 站点展示元数据：id → 名称/官网。前端维护，官网缺失时回退到来源 URL 的域名。 */

export interface SiteInfo {
  name: string;
  homepage: string;
}

const SITE_MAP: Record<string, SiteInfo> = {
  sudocode: { name: "SudoCode", homepage: "https://sudocode.chat" },
  cun: { name: "CUN.AI", homepage: "https://www.cun.ai" },
  modelflare: { name: "Modelflare", homepage: "https://modelflare.dev" },
  "downstream-jbbtoken": { name: "JBB 金贝贝", homepage: "https://downstream.jbbtoken.cn" },
  totokens: { name: "To-Tokens", homepage: "https://totokens.cc" },
  icodeeasy: { name: "I Code Easy", homepage: "https://icodeeasy.cc" },
  hao: { name: "HaoAI", homepage: "https://hao.ai" },
};

function originOf(url: string): string | null {
  try {
    return new URL(url).origin;
  } catch {
    return null;
  }
}

/** 解析站点信息；映射表没有的 id 回退到 id 本身 + 来源 URL 域名。 */
export function getSiteInfo(siteId: string, sourceUrl?: string): SiteInfo {
  const known = SITE_MAP[siteId];
  if (known) return known;
  const fallback = sourceUrl ? originOf(sourceUrl) : null;
  return {
    name: siteId,
    homepage: fallback ?? "",
  };
}
