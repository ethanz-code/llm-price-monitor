/** 结构化数据出口：以 <script type="application/ld+json"> 注入 JSON-LD，供搜索引擎与 AI 摘要读取。 */
export function JsonLd({ data }: { data: Record<string, unknown> }) {
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(data).replace(/</g, "\\u003c") }}
    />
  );
}
