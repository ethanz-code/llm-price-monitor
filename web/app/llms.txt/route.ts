/** /llms.txt：给大模型的站点索引（llmstxt.org 口径），内容构造见 lib/llms。 */
import { apiGetOptional } from "@/lib/api";
import { buildLlmsTxt } from "@/lib/llms";
import { siteOrigin } from "@/lib/seo";
import type { MetaData } from "@/lib/types";

export async function GET(): Promise<Response> {
  const origin = await siteOrigin();
  const meta = await apiGetOptional<MetaData>("/api/meta").catch(() => null);
  return new Response(buildLlmsTxt(origin, meta?.sites ?? []), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
}
