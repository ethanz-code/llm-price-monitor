/** /llms-full.txt：llms.txt 的完整版（数据口径 + 页面明细 + 常见问题全文），内容构造见 lib/llms。 */
import { apiGetOptional, PUBLIC_REVALIDATE } from "@/lib/api";
import { buildLlmsFullTxt } from "@/lib/llms";
import { siteOrigin } from "@/lib/seo";
import type { MetaData } from "@/lib/types";

export async function GET(): Promise<Response> {
  const origin = await siteOrigin();
  const meta = await apiGetOptional<MetaData>("/api/meta", PUBLIC_REVALIDATE).catch(() => null);
  return new Response(buildLlmsFullTxt(origin, meta?.sites ?? []), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
}
