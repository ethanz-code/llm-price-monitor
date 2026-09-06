"use client";

/** 管理面板文档页：读仓库 README.md 并按站点设计体系渲染。 */

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import remarkAlert from "remark-github-blockquote-alert";
import { apiSend } from "@/lib/api";

/** README 里的图片是仓库相对路径（如 web/app/icon.svg）：
 *  GitHub 上按仓库根解析，这里映射到站点根由 Next 静态路由提供。 */
function rewriteImageSrc(src: string | undefined): string | undefined {
  if (!src || /^(https?:|data:|\/)/.test(src)) return src;
  if (src.startsWith("web/app/")) return `/${src.slice("web/app/".length)}`;
  return src;
}

export function AdminDocs() {
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    apiSend<{ markdown: string }>("/api/docs/readme", "GET")
      .then((data) => setMarkdown(data.markdown))
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  if (error) {
    return (
      <div className="panel" style={{ padding: "24px 28px" }}>
        <span style={{ fontSize: 13, color: "var(--text-2)" }}>文档加载失败：{error}</span>
      </div>
    );
  }
  if (markdown === null) {
    return (
      <div className="panel" style={{ padding: "24px 28px" }}>
        <span style={{ color: "var(--text-2)", fontSize: 13 }}>加载中…</span>
      </div>
    );
  }
  return (
    <div className="panel markdown-doc">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkAlert]}
        rehypePlugins={[rehypeRaw]}
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
          img: ({ src, alt, width }) => (
            <img src={rewriteImageSrc(typeof src === "string" ? src : undefined)} alt={alt ?? ""} width={width} />
          ),
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
